#!/usr/bin/env python3
"""
Automatic Frequency Switching System for Ubiquiti PowerBeam M5 devices
Este script monitorea la calidad del enlace y cambia automáticamente la frecuencia
cuando detecta interferencia o degradación de la señal.
"""
# Configuración de dispositivos
MASTER_IP = "10.20.5.17"  # Cambiar a la IP de tu radio maestro
SLAVE_IP = "10.20.5.18"   # Cambiar a la IP de tu radio esclavo
USERNAME = "ubnt"           # Usuario por defecto de Ubiquiti
PASSWORD = "tupassword"       # Cambiar a tu contraseña

import requests
import paramiko
import time
import logging
import json
import random
import subprocess
import sys
import os
from datetime import datetime

# Configuración de logging
log_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'frequency_switcher.log')
logging.basicConfig(
    filename=log_file,
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)


# Lista de frecuencias disponibles (en MHz)
AVAILABLE_FREQUENCIES = [5665, 5675, 5685, 5695, 5710, 5760, 5780, 5830, 5835]

# Umbrales de calidad para cambio de frecuencia
SIGNAL_THRESHOLD = -70       # dBm - Si la señal cae debajo de este valor
CCQ_THRESHOLD = 70           # % - Si la calidad de conexión cae debajo de este valor
TX_CAPACITY_THRESHOLD = 50   # % - Si la capacidad de transmisión cae debajo de este %

# Período entre verificaciones (en segundos)
CHECK_INTERVAL = 300  # 5 minutos

# Función para obtener el estado del dispositivo usando la API HTTP de AirOS
def get_device_status(ip_address, username, password):
    """
    Obtiene el estado del dispositivo usando la API HTTP de AirOS,
    que es mucho más confiable que la conexión SSH para obtener datos
    """
    try:
        # Deshabilitamos la verificación de certificados SSL
        requests.packages.urllib3.disable_warnings(requests.packages.urllib3.exceptions.InsecureRequestWarning)

        # Primero intentamos con la API status.cgi (AirOS 6.x y superior)
        session = requests.Session()
        session.verify = False  # Deshabilitar verificación SSL

        # Realizar login
        login_url = f"http://{ip_address}/login.cgi"
        login_data = {"username": username, "password": password}
        login_response = session.post(login_url, data=login_data, timeout=10)

        if login_response.status_code != 200:
            logging.error(f"Error de autenticación HTTP {login_response.status_code}")
            return None

        # Obtener datos de status.cgi
        status_url = f"http://{ip_address}/status.cgi"
        status_response = session.get(status_url, timeout=10)

        if status_response.status_code == 200:
            try:
                data = status_response.json()
                logging.info(f"Datos obtenidos exitosamente vía API status.cgi")
                return data
            except json.JSONDecodeError:
                logging.warning("Respuesta no es JSON válido, intentando con otro método")

        # Si falla, intentamos con la API alternativa (AirOS 8.x)
        status_url_alt = f"http://{ip_address}/js/status.js"
        status_response = session.get(status_url_alt, timeout=10)

        if status_response.status_code == 200:
            try:
                # Extraer el JSON de la respuesta que puede estar dentro de JavaScript
                text = status_response.text
                json_start = text.find('{')
                json_end = text.rfind('}') + 1
                if json_start >= 0 and json_end > json_start:
                    json_str = text[json_start:json_end]
                    data = json.loads(json_str)
                    logging.info(f"Datos obtenidos exitosamente vía API status.js")
                    return data
                else:
                    logging.error("No se pudo extraer JSON de la respuesta")
            except Exception as e:
                logging.error(f"Error al procesar status.js: {str(e)}")

        # Si ambos métodos fallan, intentamos con info.cgi (más antiguo)
        info_url = f"http://{ip_address}/info.cgi"
        info_response = session.get(info_url, timeout=10)

        if info_response.status_code == 200:
            try:
                data = info_response.json()
                logging.info(f"Datos obtenidos exitosamente vía API info.cgi")
                return data
            except:
                logging.error("No se pudo obtener datos de info.cgi")

        logging.error(f"No se pudo obtener estado del dispositivo {ip_address}")
        return None

    except Exception as e:
        logging.error(f"Error al obtener estado vía HTTP: {str(e)}")
        return None

# Función para extraer los datos relevantes del estado del dispositivo
def parse_device_status(data):
    """
    Extrae los datos relevantes de la respuesta JSON del dispositivo
    Maneja diferentes versiones de la API de AirOS
    """
    if not data:
        return None

    status = {
        "device_name": None,
        "signal_level": None,
        "ccq": None,
        "frequency": None,
        "tx_capacity": None,
        "channel_width": None,
        "noise_floor": None,
        "distance": None,
        "uptime": None,
        "mode": None,
        "tx_power": None
    }

    # Determinar la estructura de datos basada en las claves presentes
    if 'wireless' in data:
        # AirOS 6.x y superior
        wireless = data.get('wireless', {})
        host = data.get('host', {})

        status["device_name"] = host.get('hostname')
        status["signal_level"] = wireless.get('signal')
        status["ccq"] = wireless.get('ccq')
        status["frequency"] = wireless.get('frequency')
        status["tx_capacity"] = wireless.get('txcapacity')
        status["channel_width"] = wireless.get('chanbw')
        status["noise_floor"] = wireless.get('noisef')
        status["distance"] = wireless.get('distance')
        status["mode"] = wireless.get('mode')
        status["tx_power"] = wireless.get('txpower')

        if 'uptime' in host:
            status["uptime"] = host.get('uptime')

    elif 'status' in data and 'wireless' in data['status']:
        # Otro formato posible de AirOS
        wireless = data['status'].get('wireless', {})

        status["signal_level"] = wireless.get('signal')
        status["ccq"] = wireless.get('ccq')
        status["frequency"] = wireless.get('frequency')
        status["channel_width"] = wireless.get('chwidth')

    elif 'signal' in data:
        # Formato más simple/antiguo
        status["signal_level"] = data.get('signal')
        status["ccq"] = data.get('ccq')
        status["frequency"] = data.get('frequency')

    return status

# Función para realizar un cambio de frecuencia utilizando API HTTP
def change_frequency_http(ip_address, username, password, new_frequency):
    """
    Cambia la frecuencia del dispositivo utilizando la API HTTP,
    que funciona en la mayoría de las versiones de AirOS
    """
    try:
        # Deshabilitamos la verificación de certificados SSL
        requests.packages.urllib3.disable_warnings(requests.packages.urllib3.exceptions.InsecureRequestWarning)

        session = requests.Session()
        session.verify = False  # Deshabilitar verificación SSL

        # Realizar login
        login_url = f"http://{ip_address}/login.cgi"
        login_data = {"username": username, "password": password}
        login_response = session.post(login_url, data=login_data, timeout=10)

        if login_response.status_code != 200:
            logging.error(f"Error de autenticación HTTP {login_response.status_code}")
            return False

        # Determinar si es necesario obtener algún token/csrf primero
        token = None
        try:
            main_page = session.get(f"http://{ip_address}/", timeout=10)
            if 'csrf_token' in main_page.text:
                for line in main_page.text.split('\n'):
                    if 'csrf_token' in line:
                        token_start = line.find('value="') + 7
                        token_end = line.find('"', token_start)
                        if token_start > 7 and token_end > token_start:
                            token = line[token_start:token_end]
                            break
        except:
            # Si falla, intentamos sin token
            pass

        # Preparar datos para el cambio de frecuencia
        config_data = {
            "radio.1.freq": str(new_frequency)
        }

        # Agregar token si existe
        if token:
            config_data["csrf_token"] = token

        # Intentar cambiar con varios endpoints posibles
        endpoints = [
            "/cfg.cgi",  # AirOS 6.x
            "/config.cgi",  # Más antiguo
            "/wireless.cgi"  # Otro posible endpoint
        ]

        success = False
        for endpoint in endpoints:
            try:
                config_url = f"http://{ip_address}{endpoint}"
                response = session.post(config_url, data=config_data, timeout=15)

                if response.status_code == 200:
                    logging.info(f"Frecuencia cambiada exitosamente usando {endpoint}")
                    success = True
                    break
                else:
                    logging.warning(f"Intento fallido usando {endpoint}: HTTP {response.status_code}")
            except Exception as e:
                logging.error(f"Error al cambiar frecuencia vía {endpoint}: {str(e)}")

        if success:
            # En algunos dispositivos, necesitamos aplicar los cambios explícitamente
            try:
                apply_url = f"http://{ip_address}/apply.cgi"
                apply_data = {"commit": "1"}
                if token:
                    apply_data["csrf_token"] = token

                apply_response = session.post(apply_url, data=apply_data, timeout=30)
                if apply_response.status_code == 200:
                    logging.info(f"Cambios aplicados exitosamente")
                    # Esperar a que se apliquen los cambios
                    time.sleep(5)
            except Exception as e:
                logging.warning(f"Error al aplicar cambios (puede que no sea necesario): {str(e)}")

        return success

    except Exception as e:
        logging.error(f"Error general al cambiar frecuencia: {str(e)}")
        return False

# Función para obtener y mostrar información detallada del enlace
def display_link_info(ip_address, username, password):
    """
    Obtiene y muestra información detallada sobre el estado del enlace PtP
    """
    try:
        # Obtener información del dispositivo vía HTTP
        device_data = get_device_status(ip_address, username, password)
        status = parse_device_status(device_data)

        if not status:
            logging.error(f"No se pudo obtener información del dispositivo {ip_address}")
            info_msg = f"""
=== INFORMACIÓN DETALLADA DEL ENLACE ===
IP: {ip_address}
Estado: Error al obtener datos
========================================
"""
            logging.info(info_msg)
            print(info_msg)
            return None

        # Mostrar información formateada
        info_msg = f"""
=== INFORMACIÓN DETALLADA DEL ENLACE ===
IP: {ip_address}
Dispositivo: {status.get('device_name', 'Desconocido')}
Modo: {status.get('mode', 'Desconocido')}
Frecuencia: {status.get('frequency', 'Desconocido')} MHz
Ancho de canal: {status.get('channel_width', 'Desconocido')} MHz
Señal: {status.get('signal_level', 'Desconocido')} dBm
CCQ: {status.get('ccq', 'Desconocido')}%
Potencia TX: {status.get('tx_power', 'Desconocido')} dBm
Capacidad TX: {status.get('tx_capacity', 'Desconocido')}%
Piso de ruido: {status.get('noise_floor', 'Desconocido')} dBm
Distancia: {status.get('distance', 'Desconocido')}
Tiempo activo: {status.get('uptime', 'Desconocido')}
========================================
"""
        logging.info(info_msg)
        print(info_msg)

        return status
    except Exception as e:
        error_msg = f"Error al obtener información detallada del enlace: {str(e)}"
        logging.error(error_msg)
        print(error_msg)
        return None

# Función para escanear interferencias (usando métodos alternativos si airView no está disponible)
def find_best_frequency(ip_address, username, password, current_frequency):
    """
    Encuentra la mejor frecuencia disponible
    Si no puede escanear, selecciona una frecuencia aleatoria diferente a la actual
    """
    # Por defecto, elegir una frecuencia aleatoria diferente a la actual
    available_options = [f for f in AVAILABLE_FREQUENCIES if f != current_frequency]
    if not available_options:
        return AVAILABLE_FREQUENCIES[0]  # Si solo hay una frecuencia, usar esa

    return random.choice(available_options)

# Función principal para monitoreo continuo
def monitor_and_switch():
    """Monitorea la calidad del enlace y cambia la frecuencia si es necesario"""
    current_frequency = None
    consecutive_failures = 0

    while True:
        try:
            logging.info("Verificando estado del enlace...")

            # Mostrar información detallada del enlace maestro
            logging.info("Obteniendo información detallada del radio maestro...")
            master_details = display_link_info(MASTER_IP, USERNAME, PASSWORD)

            # Mostrar información detallada del enlace esclavo
            logging.info("Obteniendo información detallada del radio esclavo...")
            slave_details = display_link_info(SLAVE_IP, USERNAME, PASSWORD)

            # Verificar si tenemos información válida para evaluar el enlace
            if master_details and 'signal_level' in master_details and master_details['signal_level'] is not None:
                current_frequency = master_details.get('frequency')
                signal_level = master_details.get('signal_level')
                ccq = master_details.get('ccq')
                tx_capacity = master_details.get('tx_capacity')

                logging.info(f"Estado actual: Señal={signal_level}dBm, "
                             f"CCQ={ccq}%, Frecuencia={current_frequency}MHz")

                # Verificar si se requiere cambio de frecuencia
                need_change = False

                if signal_level is not None and isinstance(signal_level, (int, float)) and signal_level < SIGNAL_THRESHOLD:
                    logging.warning(f"Señal ({signal_level} dBm) por debajo del umbral ({SIGNAL_THRESHOLD} dBm)")
                    need_change = True

                if ccq is not None and isinstance(ccq, (int, float)) and ccq < CCQ_THRESHOLD:
                    logging.warning(f"CCQ ({ccq}%) por debajo del umbral ({CCQ_THRESHOLD}%)")
                    need_change = True

                if tx_capacity is not None and isinstance(tx_capacity, (int, float)) and tx_capacity < TX_CAPACITY_THRESHOLD:
                    logging.warning(f"Capacidad TX ({tx_capacity}%) por debajo del umbral ({TX_CAPACITY_THRESHOLD}%)")
                    need_change = True

                if need_change:
                    consecutive_failures += 1
                    logging.warning(f"Calidad del enlace por debajo del umbral ({consecutive_failures}/3)")

                    # Cambiar frecuencia después de 3 verificaciones fallidas consecutivas
                    if consecutive_failures >= 3:
                        logging.warning("Iniciando cambio de frecuencia...")

                        # Encontrar la mejor frecuencia
                        best_frequency = find_best_frequency(MASTER_IP, USERNAME, PASSWORD, current_frequency)

                        logging.info(f"Cambiando frecuencia de {current_frequency}MHz a {best_frequency}MHz")

                        # Cambiar frecuencia primero en el esclavo y luego en el maestro
                        # (esto minimiza el tiempo de desconexión)
                        slave_success = change_frequency_http(SLAVE_IP, USERNAME, PASSWORD, best_frequency)
                        time.sleep(15)  # Esperar a que el esclavo se estabilice

                        if slave_success:
                            master_success = change_frequency_http(MASTER_IP, USERNAME, PASSWORD, best_frequency)

                            if master_success:
                                logging.info("Cambio de frecuencia completado exitosamente")
                                consecutive_failures = 0
                            else:
                                logging.error("Error al cambiar la frecuencia del maestro")
                        else:
                            logging.error("Error al cambiar la frecuencia del esclavo")
                else:
                    # Enlace estable, reiniciar contador
                    if consecutive_failures > 0:
                        logging.info("Enlace estable, reiniciando contador de fallos")
                    consecutive_failures = 0
            else:
                logging.error("No se pudo obtener información válida del enlace")
                consecutive_failures += 1

            # Esperar antes de la próxima verificación
            logging.info(f"Esperando {CHECK_INTERVAL} segundos hasta la próxima verificación...")
            time.sleep(CHECK_INTERVAL)

        except Exception as e:
            logging.error(f"Error en el ciclo de monitoreo: {str(e)}")
            time.sleep(60)  # Esperar un minuto antes de reintentar

# Función para mostrar estado actual sin ejecutar el servicio completo
def show_current_status():
    """Muestra el estado actual de los enlaces sin iniciar el servicio completo"""
    print("=== ESTADO ACTUAL DE LOS ENLACES PTP ===")
    print("Obteniendo información del radio maestro...")
    master_info = display_link_info(MASTER_IP, USERNAME, PASSWORD)

    print("\nObteniendo información del radio esclavo...")
    slave_info = display_link_info(SLAVE_IP, USERNAME, PASSWORD)

    # Verificar si hay problemas potenciales
    if master_info and 'signal_level' in master_info and master_info['signal_level'] is not None:
        try:
            signal = float(master_info['signal_level'])
            if signal < SIGNAL_THRESHOLD:
                print(f"\n⚠️ ADVERTENCIA: Señal ({signal} dBm) por debajo del umbral ({SIGNAL_THRESHOLD} dBm)")
        except:
            pass

    if master_info and 'ccq' in master_info and master_info['ccq'] is not None:
        try:
            ccq = float(master_info['ccq'])
            if ccq < CCQ_THRESHOLD:
                print(f"\n⚠️ ADVERTENCIA: CCQ ({ccq}%) por debajo del umbral ({CCQ_THRESHOLD}%)")
        except:
            pass

    print("\n=== INFORMACIÓN DE FRECUENCIAS DISPONIBLES ===")
    print(f"Frecuencias configuradas: {AVAILABLE_FREQUENCIES}")
    if master_info and 'frequency' in master_info and master_info['frequency'] is not None:
        current_freq = master_info['frequency']
        print(f"Frecuencia actual: {current_freq} MHz")
    print("================================================")

# Función para probar la conexión con los dispositivos
def test_device_connection():
    """Prueba la conexión con los dispositivos y muestra información básica"""
    print("=== PROBANDO CONEXIÓN CON DISPOSITIVOS ===")

    # Probar maestro
    print(f"\nProbando conexión con el dispositivo maestro ({MASTER_IP})...")
    try:
        # Deshabilitamos la verificación de certificados SSL
        response = requests.get(f"http://{MASTER_IP}", timeout=5, verify=False)
        if response.status_code == 200:
            print(f"✅ Conexión exitosa al maestro ({MASTER_IP})")
        else:
            print(f"❌ Error al conectar al maestro: Código HTTP {response.status_code}")
    except Exception as e:
        print(f"❌ Error al conectar al maestro: {str(e)}")

    # Probar esclavo
    print(f"\nProbando conexión con el dispositivo esclavo ({SLAVE_IP})...")
    try:
        # Deshabilitamos la verificación de certificados SSL
        response = requests.get(f"http://{SLAVE_IP}", timeout=5, verify=False)
        if response.status_code == 200:
            print(f"✅ Conexión exitosa al esclavo ({SLAVE_IP})")
        else:
            print(f"❌ Error al conectar al esclavo: Código HTTP {response.status_code}")
    except Exception as e:
        print(f"❌ Error al conectar al esclavo: {str(e)}")

    # Probar API status
    print("\nProbando API de status en dispositivos...")

    # Probar API en maestro
    print(f"Probando API en maestro ({MASTER_IP})...")
    try:
        session = requests.Session()
        # Deshabilitamos la verificación de certificados SSL para toda la sesión
        session.verify = False
        # Suprimir advertencias de solicitudes inseguras
        requests.packages.urllib3.disable_warnings(requests.packages.urllib3.exceptions.InsecureRequestWarning)

        login_data = {"username": USERNAME, "password": PASSWORD}
        session.post(f"http://{MASTER_IP}/login.cgi", data=login_data, timeout=5)

        endpoints = ["/status.cgi", "/js/status.js", "/info.cgi"]
        for endpoint in endpoints:
            try:
                response = session.get(f"http://{MASTER_IP}{endpoint}", timeout=5)
                if response.status_code == 200:
                    print(f"✅ API {endpoint} en maestro: Accesible")
                else:
                    print(f"❌ API {endpoint} en maestro: Error HTTP {response.status_code}")
            except Exception as e:
                print(f"❌ API {endpoint} en maestro: {str(e)}")
    except Exception as e:
        print(f"❌ Error al probar API en maestro: {str(e)}")

    # Probar API en esclavo
    print(f"\nProbando API en esclavo ({SLAVE_IP})...")
    try:
        session = requests.Session()
        # Deshabilitamos la verificación de certificados SSL para toda la sesión
        session.verify = False

        login_data = {"username": USERNAME, "password": PASSWORD}
        session.post(f"http://{SLAVE_IP}/login.cgi", data=login_data, timeout=5)

        endpoints = ["/status.cgi", "/js/status.js", "/info.cgi"]
        for endpoint in endpoints:
            try:
                response = session.get(f"http://{SLAVE_IP}{endpoint}", timeout=5)
                if response.status_code == 200:
                    print(f"✅ API {endpoint} en esclavo: Accesible")
                else:
                    print(f"❌ API {endpoint} en esclavo: Error HTTP {response.status_code}")
            except Exception as e:
                print(f"❌ API {endpoint} en esclavo: {str(e)}")
    except Exception as e:
        print(f"❌ Error al probar API en esclavo: {str(e)}")

    print("\n=== FIN DE PRUEBAS DE CONEXIÓN ===")

# Función para ejecutar como servicio
def run_as_service():
    """Ejecuta el script como un servicio"""
    logging.info("Iniciando servicio de cambio automático de frecuencia")
    print("Servicio de cambio automático de frecuencia iniciado")
    print(f"Registros disponibles en: {log_file}")
    monitor_and_switch()

if __name__ == "__main__":
    # Verificar argumentos de línea de comandos
    if len(sys.argv) > 1:
        if sys.argv[1] == '--status':
            # Solo mostrar el estado actual
            show_current_status()
        elif sys.argv[1] == '--test':
            # Probar la conexión con los dispositivos
            test_device_connection()
        else:
            print(f"Argumento desconocido: {sys.argv[1]}")
            print("Uso: python frequency_switcher.py [--status|--test]")
    else:
        # Ejecutar el servicio completo
        run_as_service()
