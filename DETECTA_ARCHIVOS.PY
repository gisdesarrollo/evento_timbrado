from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from datetime import datetime
from num2words import num2words
from decimal import Decimal, ROUND_DOWN
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import List
from email.utils import formataddr
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
import requests
import json
import time
import os
import sys
import json
import shutil
import logging
import smtplib
import tempfile


@dataclass
class XSAConfig:
    server: str
    key: str
    tipoDocumento: str
    idSucursal: str

@dataclass
class EDocsConfig:
    cwemail: str
    etiquetaDocumento: str

@dataclass
class OrganizacionConfig:
    nombre: str
    xsa: XSAConfig
    notificacionesError: List[str]
    edocs: EDocsConfig
    
# Ruta base del script
base_dir = os.path.dirname(os.path.abspath(__file__))

# Rutas absolutas
log_path = os.path.join(base_dir, "log_cfdi.txt")
json_path = os.path.join(base_dir, "config.json")
carpeta_respaldo = os.path.join(base_dir, "respaldo")
subcarpetas = ["enviados", "errores"]
for sub in subcarpetas:
    ruta = os.path.join(carpeta_respaldo, sub)
    os.makedirs(ruta, exist_ok=True)

logging.basicConfig(
    filename=log_path,
    filemode='a',
    format='%(asctime)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logging.info("=== Inicio del log CFDI ===")

class DataSmtp:
    def __init__(self):
        self.smtp_username = "AKIAUME4NVOFWLTUNYQT"
        self.smtp_password = "BNyUDFbJmmAlER5dEyZ6DUDk4uW66PxYrCF3g9Fa3vTq"
        self.smtp_host = "email-smtp.us-east-2.amazonaws.com"
        self.smtp_port = 587 # STARTTLS
        self.configset = "my-first-configuration-set"
        
class DataRequest:
    def __init__(self, idSucursal, idTipoCfd, nombre, archivoFuente):
        self.idSucursal = idSucursal
        self.idTipoCfd = idTipoCfd
        self.nombre = nombre
        self.archivoFuente = archivoFuente

def leer_configuracion_json(path: str) -> List['OrganizacionConfig']:
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        orgs = data['config']['orgs']
        configuraciones = []

        for nombre, datos in orgs.items():
            config = OrganizacionConfig(
                nombre=nombre,
                xsa=XSAConfig(**datos['xsa']),
                notificacionesError=datos['notificacionesError'],
                edocs=EDocsConfig(**datos['eDocs'])
            )
            configuraciones.append(config)

        return configuraciones

    except FileNotFoundError:
        logging.error(f"Error: No se encontró el archivo de configuración en {path}")
        sys.exit(1)
    except json.JSONDecodeError as e:
        logging.error(f"Error: El archivo de configuración no es un JSON válido.\nDetalles: {e}")
        sys.exit(1)
    except KeyError as e:
        logging.error(f"Error: Clave faltante en el JSON: {e}")
        sys.exit(1)
    except TypeError as e:
        logging.error(f"Error al construir la configuración: {e}")
        sys.exit(1)
    except Exception as e:
        logging.error(f"Error inesperado al leer configuración: {e}")
        sys.exit(1)

configs = leer_configuracion_json(json_path)
def generacion_cfdi(data_nodo: dict, path: str) -> str:
    carpeta_enviados = os.path.join(carpeta_respaldo, "enviados")
    carpeta_errores = os.path.join(carpeta_respaldo, "errores")
    
    # Obtener valor de los campos
    organizationCodeBranchAddress = data_nodo.get("BranchOrganizationCode")
    transactionType = data_nodo.get("TransactionType")
    number = data_nodo.get("Number")
    organizationCodeOrganizationAddress = data_nodo.get("OrganizationOrganizationCode")
    nombre_archivo = f"{organizationCodeBranchAddress}_{transactionType}_{number}_{organizationCodeOrganizationAddress}.xml"
    logging.info(f"Archivo '{nombre_archivo}' renombrado éxito.")
    
    #contenido xml
    contenido = read_xml(path)
    #**** Obtener la lista de organizaciones JSON****
    for org_config in configs:
        logging.info(f"Obteniendo datos de Organización: {org_config.nombre}")  
        xsa = org_config.xsa
        email_error = org_config.notificacionesError
        email_successful = org_config.edocs
        
        if not xsa.key:
            raise Exception("Error: Key NULL")
        if not xsa.tipoDocumento:
            raise Exception("Error: Tipo Documento XSA NULL")
        if not xsa.idSucursal:
            raise Exception("Error: Id Sucursal XSA NULL")

        url_xsa = f"{xsa.server}:9050/{xsa.key}/cfdis"
    
        data_json_xsa = DataRequest(
            idSucursal=xsa.idSucursal,
            idTipoCfd=xsa.tipoDocumento,
            nombre=nombre_archivo,
            archivoFuente=contenido
        )

        json_string = json.dumps(data_json_xsa.__dict__)
        status_text = "ERROR"
        file_path_temp = ""
        try:
            response = requests.put(
                url_xsa,
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json"
                },
                data=json_string,
                verify=False
            )
            response.raise_for_status()
            if response.status_code == 200:
                shutil.move(path, os.path.join(carpeta_enviados, nombre_archivo))
                status_text = "OK"
                logging.info(f"Archivo '{nombre_archivo}' timbrado con éxito.")
                
                data_json = json.loads(response.text)  # Convertir a diccionario
                xml_url = f"{xsa.server}:9050" + data_json.get("xmlDownload")
                file_path_temp = download_file(xml_url, nombre_archivo)
                send_email(email_successful,email_error,file_path_temp,nombre_archivo,status_text,"")
                return data_json
            else:
                logging.info(f"FALLÓ con status {response.status_code}: {response.text}")
                shutil.move(path, os.path.join(carpeta_errores, nombre_archivo))
                status_text = "ERROR"
                send_email(email_successful,email_error,file_path_temp,nombre_archivo,status_text,response.text)
                
                return {
                    "error": f"FALLÓ con status {response.status_code}",
                    "response_text": response.text
                }
        except requests.exceptions.RequestException as ex:
            shutil.move(path, os.path.join(carpeta_errores, nombre_archivo))
            status_text = "ERROR"
            send_email(email_successful,email_error,file_path_temp,nombre_archivo,status_text,"Error Interno")
            raise Exception(f"ERROR: {str(ex)}")
    
def read_xml_schemas(xml_path: str)-> str:
    # Cargar el archivo XML
    tree = ET.parse(xml_path)
    root = tree.getroot()
    # Definir el namespace
    ns = {'ns': 'http://www.cargowise.com/Schemas/Universal/2011/11'}
    return ns,root,tree

def read_xml(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as archivo:
            contenido = archivo.read()
        return contenido
    except FileNotFoundError:
        logging.error(f"Archivo no encontrado: {path}")
    except Exception as e:
        logging.error(f"Error al leer el archivo: {e}")

        
def getTransactionInfoData(ns: dict, root: ET.Element) -> dict:
    data = {
        "BranchOrganizationCode": None,
        "TransactionType": None,
        "Number": None,
        "OrganizationOrganizationCode": None
    }

    # BranchAddress/OrganizationCode
    try:
        node = root.find('.//ns:TransactionInfo/ns:BranchAddress/ns:OrganizationCode', ns)
        data["BranchOrganizationCode"] = node.text if node is not None else None
        if node is None:
            raise ValueError("Nodo BranchAddress/OrganizationCode no encontrado")
    except Exception as e:
        logging.error(f"Error obteniendo BranchOrganizationCode: {e}")

    # TransactionType
    try:
        node = root.find('.//ns:TransactionInfo/ns:TransactionType', ns)
        data["TransactionType"] = node.text if node is not None else None
        if node is None:
            raise ValueError("Nodo TransactionType no encontrado")
    except Exception as e:
        logging.error(f"Error obteniendo TransactionType: {e}")

    # Number
    try:
        node = root.find('.//ns:TransactionInfo/ns:Number', ns)
        data["Number"] = node.text if node is not None else None
        if node is None:
            raise ValueError("Nodo Number no encontrado")
    except Exception as e:
        logging.error(f"Error obteniendo Number: {e}")

    # OrganizationAddress/OrganizationCode
    try:
        node = root.find('.//ns:TransactionInfo/ns:OrganizationAddress/ns:OrganizationCode', ns)
        data["OrganizationOrganizationCode"] = node.text if node is not None else None
        if node is None:
            raise ValueError("Nodo OrganizationAddress/OrganizationCode no encontrado")
    except Exception as e:
        logging.error(f"Error obteniendo OrganizationOrganizationCode: {e}")

    return data

def agregar_importe_letra(xml_path: str):
    ns, root, tree = read_xml_schemas(xml_path)

    # Buscar nodos
    transaction_info = root.find('.//ns:TransactionInfo', ns)
    # Verificar si ya existe el nodo ImporteLetra
    existing_node = transaction_info.find('ns:ImporteLetra', ns)
    if existing_node is not None:
        return
    ostotal = transaction_info.find('ns:OSTotal', ns)
    currency = transaction_info.find('ns:OSCurrency/ns:Code', ns)

    # Validar existencia
    if ostotal is None or currency is None:
        raise ValueError("OSTotal o Currency no encontrados en el XML.")
    # Procesar valores
    importe = Decimal(ostotal.text).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
    moneda = currency.text
    entero = int(importe)
    centavos = int((importe - entero) * 100)
    # Convertir a letras
    letra = num2words(entero, lang='es').upper()
    importe_letra = f"{letra} {centavos:02d}/100 {moneda}"
    # Crear e insertar nuevo nodo
    importe_letra_node = ET.Element(f"{{{ns['ns']}}}ImporteLetra")
    importe_letra_node.text = importe_letra
    transaction_info.append(importe_letra_node)
    tree.write(xml_path, encoding='utf-8', xml_declaration=True)
    
def send_email(email_successful: EDocsConfig, email_error: List[str], file_path_temp: str, nombre_archivo: str, status: str, error_message: str):
    try:
        data = DataSmtp()
        # Datos del correo
        SENDER_EMAIL = "facturas@applikt.com"
        SENDER_NAME = "Facturación Applikt"
        SUBJECT = "Resultado del procesamiento de documento"
        
        # Contenido del mensaje
        if status == "OK":
            recipients = [email_successful.cwemail]
            html_body = f"""\
            <html>
            <body>
                <h2>Archivo procesado correctamente</h2>
                <p>Se adjunta el archivo: <strong>{nombre_archivo}</strong></p>
            </body>
            </html>"""
        else:
            recipients = email_error
            html_body = f"""\
            <html>
            <body>
                <h2>Error en el procesamiento</h2>
                <p><strong>Nombre del documento:</strong> {nombre_archivo}</p>
                <p><strong>Descripción:</strong> {error_message}</p>
            </body>
            </html>"""

        # Crear mensaje
        msg = MIMEMultipart()
        msg['Subject'] = SUBJECT
        msg['From'] = formataddr((SENDER_NAME, SENDER_EMAIL))
        msg['To'] = ", ".join(recipients)
        msg['X-SES-CONFIGURATION-SET'] = data.configset
        msg.attach(MIMEText(html_body, "html"))

        # Adjuntar archivo si fue exitoso
        if status == "OK" and os.path.exists(file_path_temp):
            with open(file_path_temp, "rb") as f:
                attachment = MIMEApplication(f.read())
                attachment.add_header('Content-Disposition', 'attachment', filename=nombre_archivo)
                msg.attach(attachment)
        

        # Enviar correo usando STARTTLS
        with smtplib.SMTP(data.smtp_host, data.smtp_port) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()  # Re-identificación
            server.login(data.smtp_username, data.smtp_password)
            server.sendmail(SENDER_EMAIL, recipients, msg.as_string())

        logging.info("Correo enviado exitosamente.")

    except FileNotFoundError as e:
        logging.error(f"No se encontró el archivo XML '{file_path_temp}': {e}")
    except smtplib.SMTPAuthenticationError as e:
        logging.error("Error de autenticación SMTP: %s", e)
    except smtplib.SMTPException as e:
        logging.error("Error SMTP al enviar el correo: %s", e)
    except Exception as e:
        logging.error("Error inesperado al enviar el correo: %s", e)


def download_file(url, nombre_archivo):
    try:
        temp_dir = tempfile.gettempdir()
        ruta_destino = os.path.join(temp_dir, nombre_archivo)

        respuesta = requests.get(url, stream=True, timeout=30)
        respuesta.raise_for_status()

        with open(ruta_destino, 'wb') as archivo:
            for chunk in respuesta.iter_content(chunk_size=8192):
                if chunk:
                    archivo.write(chunk)

        logging.info(f"Archivo descargado correctamente en: {ruta_destino}")
        return ruta_destino
    except requests.exceptions.SSLError:
        logging.error("Error SSL: el certificado puede ser inválido o no confiable.")
    except Exception as e:
        logging.error(f"Error al descargar el archivo: {e}")
        return None


#evento se activa cada ves que llega un nuevo xml ala carpeta      
class Manejador(FileSystemEventHandler):
    def on_created(self, event):
        if not event.is_directory and event.src_path.lower().endswith('.xml'):
                    
            logging.info(f"Nuevo archivo: {os.path.basename(event.src_path)}")
            try:
                ns, root , tree = read_xml_schemas(event.src_path)
                data_nodo = getTransactionInfoData(ns, root)
                agregar_importe_letra(event.src_path)
                generacion_cfdi(data_nodo,event.src_path)
                
            except Exception as e:
                logging.error(f"Error procesando archivo {event.src_path}: {e}")

if __name__ == "__main__":
    observer_path = base_dir
    event_handler = Manejador()
    observer = Observer()
    observer.schedule(event_handler, path=observer_path, recursive=False)
    observer.start()
    
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()