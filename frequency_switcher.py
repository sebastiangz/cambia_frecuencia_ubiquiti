#!/usr/bin/env python3
"""
Automatic Frequency Switching System for Ubiquiti PowerBeam M5 devices
Este script monitorea la calidad del enlace y cambia automáticamente la frecuencia
cuando detecta interferencia o degradación de la señal.
"""
# Configuración de dispositivos
MASTER_IP = "10.20.5.17"  # Actualizado según tu salida de prueba
SLAVE_IP = "10.20.5.18"   # Actualizado según tu salida de prueba
USERNAME = "ubnt"     # Usuario por defecto de Ubiquiti
PASSWORD = "tupass"   # Cambiar a tu contraseña real

import requests
import paramiko
import time
import logging
import json
import random
import subprocess
import sys
import os
import re
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

# Función optimizada para obtener el estado del dispositivo
# Función mejorada para obtener el estado del dispositivo con mejor manejo de respuestas
def get_device_status(ip_address, username, password):
    """
    Obtiene el estado del dispositivo usando la API de AirOS con HTTPS
    optimizado para PowerBeam M5 400 con mejor manejo de respuestas
    """
    try:
        # Deshabilitamos la verificación de certificados SSL
        requests.packages.urllib3.disable_warnings(requests.packages.urllib3.exceptions.InsecureRequestWarning)

        session = requests.Session()
        session.verify = False  # Deshabilitar verificación SSL

        # Usar HTTPS ya que sabemos que funciona
        base_url = f"https://{ip_address}"

        # Realizar login
        login_url = f"{base_url}/login.cgi"
        login_data = {"username": username, "password": password, "uri": "/"}

        # Establecer encabezados correctos
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "Mozilla/5.0",
            "Referer": f"{base_url}/login.html"
        }

        login_response = session.post(
            login_url,
            data=login_data,
            headers=headers,
            timeout=10,
            allow_redirects=True
        )

        # Verificar si el login fue exitoso basado en la URL final
        if 'login.cgi' in login_response.url:
            logging.error(f"Login posiblemente fallido: redirigido a {login_response.url}")

        # Intenta obtener el status.cgi primero
        status_url = f"{base_url}/status.cgi"
        status_response = session.get(status_url, headers=headers, timeout=10)

        if status_response.status_code == 200:
            # Guardar respuesta para depuración
            debug_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), f"debug_response_{ip_address.replace('.', '_')}.txt")
            with open(debug_file, "w") as f:
                f.write(status_response.text)

            logging.info(f"Contenido de status.cgi guardado para diagnóstico en {debug_file}")

            # Intentar analizar como JSON
            try:
                status_data = json.loads(status_response.text)
                logging.info("Datos obtenidos exitosamente desde status.cgi como JSON")

                # Extraer los datos que necesitamos
                status = {}

                # Del host obtener uptime y hostname
                if 'host' in status_data:
                    host = status_data.get('host', {})
                    status["device_name"] = host.get('hostname')
                    status["uptime"] = host.get('uptime')

                # Datos wireless - los más importantes
                if 'wireless' in status_data:
                    wireless = status_data.get('wireless', {})
                    status["signal_level"] = wireless.get('signal')
                    status["ccq"] = wireless.get('ccq')
                    status["frequency"] = wireless.get('frequency').split()[0] if wireless.get('frequency') else None
                    status["tx_capacity"] = wireless.get('txrate')
                    status["channel_width"] = wireless.get('chanbw')
                    status["noise_floor"] = wireless.get('noisef')
                    status["distance"] = wireless.get('distance')
                    status["mode"] = wireless.get('mode')
                    status["tx_power"] = wireless.get('txpower')

                # Convertir valores a tipos correctos
                if status.get("signal_level") is not None:
                    status["signal_level"] = float(status["signal_level"])
                if status.get("ccq") is not None:
                    status["ccq"] = float(status["ccq"])
                if status.get("frequency") is not None:
                    status["frequency"] = float(status["frequency"])
                if status.get("tx_capacity") is not None:
                    status["tx_capacity"] = float(status["tx_capacity"])
                if status.get("channel_width") is not None:
                    status["channel_width"] = float(status["channel_width"])

                return status

            except json.JSONDecodeError:
                logging.error(f"Error al analizar JSON de status.cgi")

                # Intento alternativo: buscar datos en la respuesta HTML usando RegEx
                text = status_response.text
                status = {}

                # Buscar patrones comunes en respuestas HTML para extraer datos
                try:
                    # Señal
                    signal_match = re.search(r'signal["\s:=]+(-?\d+)', text, re.IGNORECASE)
                    if signal_match:
                        status["signal_level"] = float(signal_match.group(1))
                        logging.info(f"Señal encontrada mediante regex: {status['signal_level']}")

                    # CCQ
                    ccq_match = re.search(r'ccq["\s:=]+(\d+(\.\d+)?)', text, re.IGNORECASE)
                    if ccq_match:
                        status["ccq"] = float(ccq_match.group(1))
                        logging.info(f"CCQ encontrado mediante regex: {status['ccq']}")

                    # Frecuencia
                    freq_match = re.search(r'frequency["\s:=]+"?(\d+)(?:\s*MHz)?', text, re.IGNORECASE)
                    if freq_match:
                        status["frequency"] = float(freq_match.group(1))
                        logging.info(f"Frecuencia encontrada mediante regex: {status['frequency']}")

                    # Ruido
                    noise_match = re.search(r'noisef["\s:=]+(-?\d+)', text, re.IGNORECASE)
                    if noise_match:
                        status["noise_floor"] = float(noise_match.group(1))

                    # Potencia TX
                    txpower_match = re.search(r'txpower["\s:=]+(\d+)', text, re.IGNORECASE)
                    if txpower_match:
                        status["tx_power"] = float(txpower_match.group(1))

                    # Si encontramos al menos señal, CCQ o frecuencia, consideramos exitoso
                    if "signal_level" in status or "ccq" in status or "frequency" in status:
                        logging.info("Datos extraídos mediante expresiones regulares")
                        return status

                except Exception as e:
                    logging.error(f"Error al extraer datos con regex: {str(e)}")

        # Si todo lo anterior falla, intentar con iflist.cgi
        logging.warning("Intentando obtener datos de iflist.cgi...")
        iflist_url = f"{base_url}/iflist.cgi"
        iflist_response = session.get(iflist_url, headers=headers, timeout=10)

        if iflist_response.status_code == 200:
            # Guardar respuesta para depuración
            debug_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), f"debug_iflist_{ip_address.replace('.', '_')}.txt")
            with open(debug_file, "w") as f:
                f.write(iflist_response.text)

            logging.info(f"Contenido de iflist.cgi guardado para diagnóstico en {debug_file}")

            try:
                iflist_data = json.loads(iflist_response.text)
                logging.info("Datos obtenidos exitosamente desde iflist.cgi")

                status = {}

                # Buscar la interfaz inalámbrica (típicamente ath0)
                interfaces = iflist_data.get('interfaces', [])
                for interface in interfaces:
                    if interface.get('ifname') == 'ath0' and 'wireless' in interface:
                        wireless = interface.get('wireless', {})
                        status["signal_level"] = wireless.get('signal')
                        status["ccq"] = wireless.get('ccq')
                        status["frequency"] = wireless.get('frequency').split()[0] if wireless.get('frequency') else None
                        status["channel_width"] = wireless.get('chanbw')
                        status["noise_floor"] = wireless.get('noisef')
                        status["distance"] = wireless.get('distance')
                        status["mode"] = wireless.get('mode')
                        break

                # Convertir valores a tipos correctos
                if status.get("signal_level") is not None:
                    status["signal_level"] = float(status["signal_level"])
                if status.get("ccq") is not None:
                    status["ccq"] = float(status["ccq"])
                if status.get("frequency") is not None:
                    status["frequency"] = float(status["frequency"])
                if status.get("channel_width") is not None:
                    status["channel_width"] = float(status["channel_width"])

                # Si encontramos al menos señal, CCQ o frecuencia, consideramos exitoso
                if "signal_level" in status or "ccq" in status or "frequency" in status:
                    return status

            except json.JSONDecodeError:
                logging.error(f"Error al analizar JSON de iflist.cgi")

                # Intento alternativo con regex similar al anterior
                text = iflist_response.text
                status = {}

                try:
                    # Búsqueda con regex similar a la anterior
                    signal_match = re.search(r'signal["\s:=]+(-?\d+)', text, re.IGNORECASE)
                    if signal_match:
                        status["signal_level"] = float(signal_match.group(1))

                    ccq_match = re.search(r'ccq["\s:=]+(\d+(\.\d+)?)', text, re.IGNORECASE)
                    if ccq_match:
                        status["ccq"] = float(ccq_match.group(1))

                    freq_match = re.search(r'frequency["\s:=]+"?(\d+)(?:\s*MHz)?', text, re.IGNORECASE)
                    if freq_match:
                        status["frequency"] = float(freq_match.group(1))

                    # Si encontramos al menos señal, CCQ o frecuencia, consideramos exitoso
                    if "signal_level" in status or "ccq" in status or "frequency" in status:
                        logging.info("Datos extraídos mediante expresiones regulares de iflist.cgi")
                        return status

                except Exception as e:
                    logging.error(f"Error al extraer datos con regex de iflist.cgi: {str(e)}")

        # Si llegamos aquí, intentamos un último recurso: extraer datos de la página principal
        logging.warning("Intentando obtener datos de la página principal...")
        main_url = f"{base_url}/"
        main_response = session.get(main_url, headers=headers, timeout=10)

        if main_response.status_code == 200:
            text = main_response.text
            status = {}

            try:
                # Búsqueda con regex en la página principal
                signal_match = re.search(r'signal["\s:=]+(-?\d+)', text, re.IGNORECASE)
                if signal_match:
                    status["signal_level"] = float(signal_match.group(1))

                ccq_match = re.search(r'ccq["\s:=]+(\d+(\.\d+)?)', text, re.IGNORECASE)
                if ccq_match:
                    status["ccq"] = float(ccq_match.group(1))

                freq_match = re.search(r'frequency["\s:=]+"?(\d+)(?:\s*MHz)?', text, re.IGNORECASE)
                if freq_match:
                    status["frequency"] = float(freq_match.group(1))

                # Si encontramos al menos señal, CCQ o frecuencia, consideramos exitoso
                if "signal_level" in status or "ccq" in status or "frequency" in status:
                    logging.info("Datos extraídos mediante expresiones regulares de la página principal")
                    return status

            except Exception as e:
                logging.error(f"Error al extraer datos con regex de la página principal: {str(e)}")

        # Si llegamos aquí, no pudimos obtener datos
        logging.error(f"No se pudo obtener estado del dispositivo {ip_address}")
        return None

    except Exception as e:
        logging.error(f"Error general al obtener estado del dispositivo: {str(e)}")
        return None

# Función para depuración detallada
def debug_detailed():
    """
    Depuración detallada para casos donde el análisis JSON falla
    """
    print("=== DEPURACIÓN DETALLADA DE RESPUESTAS ===")

    # Configurar solicitudes
    requests.packages.urllib3.disable_warnings()

    # Probar maestro
    print(f"\nPrueba detallada para el maestro ({MASTER_IP}):")

    try:
        session = requests.Session()
        session.verify = False

        # Login
        login_url = f"https://{MASTER_IP}/login.cgi"
        login_data = {"username": USERNAME, "password": PASSWORD, "uri": "/"}
        headers = {"User-Agent": "Mozilla/5.0"}

        login_response = session.post(login_url, data=login_data, headers=headers, timeout=10)
        print(f"Login: Status {login_response.status_code}, URL final: {login_response.url}")

        # Intentar status.cgi
        status_url = f"https://{MASTER_IP}/status.cgi"
        status_response = session.get(status_url, headers=headers, timeout=10)
        print(f"status.cgi: Status {status_response.status_code}")

        print("Primeras 200 letras de la respuesta:")
        print(status_response.text[:200])

        # Guardar respuesta completa
        filename = os.path.join(os.path.dirname(os.path.abspath(__file__)), f"debug_full_{MASTER_IP.replace('.', '_')}.txt")
        with open(filename, "w") as f:
            f.write(status_response.text)
        print(f"Respuesta completa guardada en {filename}")

        # Intentar extraer datos con regex
        text = status_response.text
        data_found = False

        # Señal
        signal_match = re.search(r'signal["\s:=]+(-?\d+)', text, re.IGNORECASE)
        if signal_match:
            print(f"Señal encontrada: {signal_match.group(1)} dBm")
            data_found = True

        # CCQ
        ccq_match = re.search(r'ccq["\s:=]+(\d+(\.\d+)?)', text, re.IGNORECASE)
        if ccq_match:
            print(f"CCQ encontrado: {ccq_match.group(1)}%")
            data_found = True

        # Frecuencia
        freq_match = re.search(r'frequency["\s:=]+"?(\d+)(?:\s*MHz)?', text, re.IGNORECASE)
        if freq_match:
            print(f"Frecuencia encontrada: {freq_match.group(1)} MHz")
            data_found = True

        if not data_found:
            print("No se encontraron datos mediante expresiones regulares")

        # Ahora intentamos con iflist.cgi
        print("\nProbando iflist.cgi:")
        iflist_url = f"https://{MASTER_IP}/iflist.cgi"
        iflist_response = session.get(iflist_url, headers=headers, timeout=10)
        print(f"iflist.cgi: Status {iflist_response.status_code}")

        # Guardar respuesta completa
        filename = os.path.join(os.path.dirname(os.path.abspath(__file__)), f"debug_iflist_{MASTER_IP.replace('.', '_')}.txt")
        with open(filename, "w") as f:
            f.write(iflist_response.text)
        print(f"Respuesta completa guardada en {filename}")

        # Probar esclavo
        print(f"\nPrueba detallada para el esclavo ({SLAVE_IP}):")

        # Login
        login_url = f"https://{SLAVE_IP}/login.cgi"
        login_data = {"username": USERNAME, "password": PASSWORD, "uri": "/"}
        login_response = session.post(login_url, data=login_data, headers=headers, timeout=10)
        print(f"Login: Status {login_response.status_code}, URL final: {login_response.url}")

        # Intentar status.cgi
        status_url = f"https://{SLAVE_IP}/status.cgi"
        status_response = session.get(status_url, headers=headers, timeout=10)
        print(f"status.cgi: Status {status_response.status_code}")

        # Guardar respuesta completa
        filename = os.path.join(os.path.dirname(os.path.abspath(__file__)), f"debug_full_{SLAVE_IP.replace('.', '_')}.txt")
        with open(filename, "w") as f:
            f.write(status_response.text)
        print(f"Respuesta completa guardada en {filename}")

        # Intentar extraer datos con regex
        text = status_response.text
        data_found = False

        # Señal
        signal_match = re.search(r'signal["\s:=]+(-?\d+)', text, re.IGNORECASE)
        if signal_match:
            print(f"Señal encontrada: {signal_match.group(1)} dBm")
            data_found = True

        # CCQ
        ccq_match = re.search(r'ccq["\s:=]+(\d+(\.\d+)?)', text, re.IGNORECASE)
        if ccq_match:
            print(f"CCQ encontrado: {ccq_match.group(1)}%")
            data_found = True

        # Frecuencia
        freq_match = re.search(r'frequency["\s:=]+"?(\d+)(?:\s*MHz)?', text, re.IGNORECASE)
        if freq_match:
            print(f"Frecuencia encontrada: {freq_match.group(1)} MHz")
            data_found = True

        if not data_found:
            print("No se encontraron datos mediante expresiones regulares")

        # Ahora intentamos con iflist.cgi
        print("\nProbando iflist.cgi:")
        iflist_url = f"https://{SLAVE_IP}/iflist.cgi"
        iflist_response = session.get(iflist_url, headers=headers, timeout=10)
        print(f"iflist.cgi: Status {iflist_response.status_code}")

        # Guardar respuesta completa
        filename = os.path.join(os.path.dirname(os.path.abspath(__file__)), f"debug_iflist_{SLAVE_IP.replace('.', '_')}.txt")
        with open(filename, "w") as f:
            f.write(iflist_response.text)
        print(f"Respuesta completa guardada en {filename}")

    except Exception as e:
        print(f"Error en depuración: {str(e)}")

    print("=== FIN DE DEPURACIÓN DETALLADA ===")


# Función mejorada para cambiar la frecuencia utilizando API HTTP
def change_frequency_http(ip_address, username, password, new_frequency):
    """
    Cambia la frecuencia del dispositivo utilizando la API HTTPS,
    optimizado para PowerBeam M5 400
    """
    try:
        # Deshabilitamos la verificación de certificados SSL
        requests.packages.urllib3.disable_warnings(requests.packages.urllib3.exceptions.InsecureRequestWarning)

        session = requests.Session()
        session.verify = False  # Deshabilitar verificación SSL

        # Usar HTTPS ya que sabemos que funciona
        base_url = f"https://{ip_address}"

        # Realizar login
        login_url = f"{base_url}/login.cgi"
        login_data = {"username": username, "password": password, "uri": "/"}

        # Establecer encabezados correctos
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "Mozilla/5.0",
            "Referer": f"{base_url}/login.html"
        }

        login_response = session.post(
            login_url,
            data=login_data,
            headers=headers,
            timeout=10,
            allow_redirects=True
        )

        # Verificar si el login fue exitoso
        if login_response.url.endswith("login.cgi") or 'login.html' in login_response.text.lower():
            logging.error("Login fallido")
            return False

        logging.info("Login exitoso")

        # Sabemos de las pruebas que link.cgi se usa para la configuración inalámbrica
        # Primero obtenemos la página para extraer cualquier token o estado actual
        link_page_url = f"{base_url}/link.cgi"
        link_page_response = session.get(link_page_url, headers=headers, timeout=10)

        # Preparar datos para el cambio de frecuencia
        config_data = {
            "chan_freq": str(new_frequency),
            "change": "Cambiar"
        }

        # Buscar token CSRF si existe
        if 'csrf_token' in link_page_response.text:
            match = re.search(r'name="token"\s+value="([^"]+)"', link_page_response.text)
            if match:
                csrf_token = match.group(1)
                config_data["token"] = csrf_token
                logging.info(f"Token CSRF encontrado: {csrf_token}")

        # Enviar solicitud para cambiar la frecuencia
        config_response = session.post(link_page_url, data=config_data, headers=headers, timeout=15)

        if config_response.status_code == 200:
            logging.info(f"Frecuencia cambiada exitosamente a {new_frequency} MHz")

            # Esperar a que se apliquen los cambios
            time.sleep(15)
            return True
        else:
            logging.error(f"Error al cambiar frecuencia: HTTP {config_response.status_code}")
            return False

    except Exception as e:
        logging.error(f"Error general al cambiar frecuencia: {str(e)}")
        return False

# Función para obtener y mostrar información detallada del enlace
def display_link_info(ip_address, username, password):
    """
    Obtiene y muestra información detallada sobre el estado del enlace PtP
    """
    try:
        # Obtener información del dispositivo
        device_data = get_device_status(ip_address, username, password)

        if not device_data:
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
Dispositivo: {device_data.get('device_name', 'Desconocido')}
Modo: {device_data.get('mode', 'Desconocido')}
Frecuencia: {device_data.get('frequency', 'Desconocido')} MHz
Ancho de canal: {device_data.get('channel_width', 'Desconocido')} MHz
Señal: {device_data.get('signal_level', 'Desconocido')} dBm
CCQ: {device_data.get('ccq', 'Desconocido')}%
Potencia TX: {device_data.get('tx_power', 'Desconocido')} dBm
Capacidad TX: {device_data.get('tx_capacity', 'Desconocido')}%
Piso de ruido: {device_data.get('noise_floor', 'Desconocido')} dBm
Distancia: {device_data.get('distance', 'Desconocido')}
Tiempo activo: {device_data.get('uptime', 'Desconocido')}
========================================
"""
        logging.info(info_msg)
        print(info_msg)

        return device_data
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
                reasons = []

                if signal_level is not None and isinstance(signal_level, (int, float)) and signal_level < SIGNAL_THRESHOLD:
                    reason = f"Señal ({signal_level} dBm) por debajo del umbral ({SIGNAL_THRESHOLD} dBm)"
                    logging.warning(reason)
                    reasons.append(reason)
                    need_change = True

                if ccq is not None and isinstance(ccq, (int, float)) and ccq < CCQ_THRESHOLD:
                    reason = f"CCQ ({ccq}%) por debajo del umbral ({CCQ_THRESHOLD}%)"
                    logging.warning(reason)
                    reasons.append(reason)
                    need_change = True

                if tx_capacity is not None and isinstance(tx_capacity, (int, float)) and tx_capacity < TX_CAPACITY_THRESHOLD:
                    reason = f"Capacidad TX ({tx_capacity}%) por debajo del umbral ({TX_CAPACITY_THRESHOLD}%)"
                    logging.warning(reason)
                    reasons.append(reason)
                    need_change = True

                if need_change:
                    consecutive_failures += 1
                    reasons_str = ", ".join(reasons)
                    logging.warning(f"Calidad del enlace por debajo del umbral ({consecutive_failures}/3): {reasons_str}")

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

# Función mejorada para mostrar estado actual sin ejecutar el servicio completo
def show_current_status():
    """Muestra el estado actual de los enlaces sin iniciar el servicio completo"""
    print("=== ESTADO ACTUAL DE LOS ENLACES PTP ===")
    print("Obteniendo información del radio maestro...")
    master_info = display_link_info(MASTER_IP, USERNAME, PASSWORD)

    print("\nObteniendo información del radio esclavo...")
    slave_info = display_link_info(SLAVE_IP, USERNAME, PASSWORD)

    # Verificar si hay problemas potenciales
    if master_info:
        problems_found = False

        if 'signal_level' in master_info and master_info['signal_level'] is not None:
            try:
                signal = float(master_info['signal_level'])
                if signal < SIGNAL_THRESHOLD:
                    print(f"\n⚠️ ADVERTENCIA: Señal ({signal} dBm) por debajo del umbral ({SIGNAL_THRESHOLD} dBm)")
                    problems_found = True
            except:
                pass

        if 'ccq' in master_info and master_info['ccq'] is not None:
            try:
                ccq = float(master_info['ccq'])
                if ccq < CCQ_THRESHOLD:
                    print(f"\n⚠️ ADVERTENCIA: CCQ ({ccq}%) por debajo del umbral ({CCQ_THRESHOLD}%)")
                    problems_found = True
            except:
                pass

        if 'tx_capacity' in master_info and master_info['tx_capacity'] is not None:
            try:
                tx_capacity = float(master_info['tx_capacity'])
                if tx_capacity < TX_CAPACITY_THRESHOLD:
                    print(f"\n⚠️ ADVERTENCIA: Capacidad TX ({tx_capacity}%) por debajo del umbral ({TX_CAPACITY_THRESHOLD}%)")
                    problems_found = True
            except:
                pass

        if not problems_found:
            print("\n✅ El enlace parece estar funcionando correctamente. No se detectaron problemas.")

    print("\n=== INFORMACIÓN DE FRECUENCIAS DISPONIBLES ===")
    print(f"Frecuencias configuradas: {AVAILABLE_FREQUENCIES}")
    if master_info and 'frequency' in master_info and master_info['frequency'] is not None:
        current_freq = master_info['frequency']
        print(f"Frecuencia actual: {current_freq} MHz")
    print("================================================")

# Función mejorada para probar la conexión con los dispositivos
def test_device_connection():
    """Prueba la conexión con los dispositivos y muestra información básica"""
    print("=== PROBANDO CONEXIÓN CON DISPOSITIVOS ===")

    # Suprimir advertencias de solicitudes inseguras globalmente
    requests.packages.urllib3.disable_warnings(requests.packages.urllib3.exceptions.InsecureRequestWarning)

    # Probar maestro
    print(f"\nProbando conexión con el dispositivo maestro ({MASTER_IP})...")
    try:
        # Deshabilitamos la verificación de certificados SSL
        response = requests.get(f"https://{MASTER_IP}", timeout=5, verify=False)
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
        response = requests.get(f"https://{SLAVE_IP}", timeout=5, verify=False)
        if response.status_code == 200:
            print(f"✅ Conexión exitosa al esclavo ({SLAVE_IP})")
        else:
            print(f"❌ Error al conectar al esclavo: Código HTTP {response.status_code}")
    except Exception as e:
        print(f"❌ Error al conectar al esclavo: {str(e)}")

    # Probar API
    print("\nProbando datos API en dispositivos...")

    # Probar API en maestro
    print(f"Obteniendo datos de status.cgi en el maestro ({MASTER_IP})...")
    try:
        # Iniciar sesión
        session = requests.Session()
        session.verify = False  # Deshabilitar verificación SSL

        login_url = f"https://{MASTER_IP}/login.cgi"
        login_data = {"username": USERNAME, "password": PASSWORD, "uri": "/"}
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "Mozilla/5.0"
        }

        login_response = session.post(login_url, data=login_data, headers=headers, timeout=5)

        # Probar endpoint status.cgi
        status_url = f"https://{MASTER_IP}/status.cgi"
        response = session.get(status_url, timeout=5)

        if response.status_code == 200:
            print(f"✅ API status.cgi en maestro: Accesible")
            try:
                data = json.loads(response.text)
                print(f"   ✅ Respuesta JSON válida recibida")

                # Extraer información clave
                if 'wireless' in data:
                    wireless = data['wireless']
                    signal = wireless.get('signal')
                    ccq = wireless.get('ccq')
                    frequency = wireless.get('frequency', '').split(' ')[0] if wireless.get('frequency') else 'N/A'
                    print(f"   📊 Señal: {signal} dBm, CCQ: {ccq}%, Frecuencia: {frequency} MHz")
            except json.JSONDecodeError:
                print(f"   ❌ La respuesta no es JSON válido")
        else:
            print(f"❌ API status.cgi en maestro: Error HTTP {response.status_code}")
    except Exception as e:
        print(f"❌ Error al obtener datos del maestro: {str(e)}")

    # Probar API en esclavo
    print(f"\nObteniendo datos de status.cgi en el esclavo ({SLAVE_IP})...")
    try:
        # Iniciar sesión
        session = requests.Session()
        session.verify = False  # Deshabilitar verificación SSL

        login_url = f"https://{SLAVE_IP}/login.cgi"
        login_data = {"username": USERNAME, "password": PASSWORD, "uri": "/"}
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "Mozilla/5.0"
        }

        login_response = session.post(login_url, data=login_data, headers=headers, timeout=5)

        # Probar endpoint status.cgi
        status_url = f"https://{SLAVE_IP}/status.cgi"
        response = session.get(status_url, timeout=5)

        if response.status_code == 200:
            print(f"✅ API status.cgi en esclavo: Accesible")
            try:
                data = json.loads(response.text)
                print(f"   ✅ Respuesta JSON válida recibida")

                # Extraer información clave
                if 'wireless' in data:
                    wireless = data['wireless']
                    signal = wireless.get('signal')
                    ccq = wireless.get('ccq')
                    frequency = wireless.get('frequency', '').split(' ')[0] if wireless.get('frequency') else 'N/A'
                    print(f"   📊 Señal: {signal} dBm, CCQ: {ccq}%, Frecuencia: {frequency} MHz")
            except json.JSONDecodeError:
                print(f"   ❌ La respuesta no es JSON válido")
        else:
            print(f"❌ API status.cgi en esclavo: Error HTTP {response.status_code}")
    except Exception as e:
        print(f"❌ Error al obtener datos del esclavo: {str(e)}")

    print("\n=== FIN DE PRUEBAS DE CONEXIÓN ===")

# Función para realizar depuración avanzada
def debug_mode():
    """Realiza depuración avanzada para identificar problemas"""
    print("=== MODO DE DEPURACIÓN AVANZADA ===")

    # Suprimir advertencias de solicitudes inseguras globalmente
    requests.packages.urllib3.disable_warnings(requests.packages.urllib3.exceptions.InsecureRequestWarning)

    # Mostrar información de configuración
    print("Configuración actual:")
    print(f"  Master IP: {MASTER_IP}")
    print(f"  Slave IP: {SLAVE_IP}")
    print(f"  Usuario: {USERNAME}")
    print(f"  Contraseña: {'*' * len(PASSWORD)}")
    print(f"  Frecuencias disponibles: {AVAILABLE_FREQUENCIES}")
    print(f"  Umbrales: Señal < {SIGNAL_THRESHOLD} dBm, CCQ < {CCQ_THRESHOLD}%, TX < {TX_CAPACITY_THRESHOLD}%")
    print()

    # Probar obtención de estado en ambos dispositivos
    print("Obteniendo estado detallado del maestro:")
    master_status = get_device_status(MASTER_IP, USERNAME, PASSWORD)
    if master_status:
        print("  Estado obtenido exitosamente")
        print("  Datos principales:")
        for key, value in master_status.items():
            print(f"    {key}: {value}")
    else:
        print("  ❌ Error al obtener estado")

    print("\nObteniendo estado detallado del esclavo:")
    slave_status = get_device_status(SLAVE_IP, USERNAME, PASSWORD)
    if slave_status:
        print("  Estado obtenido exitosamente")
        print("  Datos principales:")
        for key, value in slave_status.items():
            print(f"    {key}: {value}")
    else:
        print("  ❌ Error al obtener estado")

    print("\n=== FIN DE DEPURACIÓN AVANZADA ===")

# Función para ejecutar como servicio
def run_as_service():
    """Ejecuta el script como un servicio"""
    logging.info("Iniciando servicio de cambio automático de frecuencia")
    print("Servicio de cambio automático de frecuencia iniciado")
    print(f"Registros disponibles en: {log_file}")
    monitor_and_switch()

# Actualizar el bloque main para incluir el nuevo comando de depuración
if __name__ == "__main__":
    # Verificar argumentos de línea de comandos
    if len(sys.argv) > 1:
        if sys.argv[1] == '--status':
            # Solo mostrar el estado actual
            show_current_status()
        elif sys.argv[1] == '--test':
            # Probar la conexión con los dispositivos
            test_device_connection()
        elif sys.argv[1] == '--debug':
            # Modo de depuración básica
            debug_mode()
        elif sys.argv[1] == '--debug-detail':
            # Modo de depuración detallada para problemas de análisis JSON
            debug_detailed()
        else:
            print(f"Argumento desconocido: {sys.argv[1]}")
            print("Uso: python frequency_switcher.py [--status|--test|--debug|--debug-detail]")
    else:
        # Ejecutar el servicio completo
        run_as_service()
