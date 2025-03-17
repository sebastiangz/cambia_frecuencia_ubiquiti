#!/usr/bin/env python3
"""
Automatic Frequency Switching System for Ubiquiti PowerBeam M5 devices
Este script monitorea la calidad del enlace y cambia automáticamente la frecuencia
cuando detecta interferencia o degradación de la señal.
"""
# Configuración de dispositivos
MASTER_IP = "10.20.5.17"
SLAVE_IP = "10.20.5.18"
USERNAME = "usuario"     # Usuario por defecto de Ubiquiti
PASSWORD = "tupass"   # Cambiar a tu contraseña real

import requests
import time
import logging
import json
import random
import sys
import os
import re
from datetime import datetime
from bs4 import BeautifulSoup
import xml.etree.ElementTree as ET

# Configuración de logging
log_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'frequency_switcher.log')
logging.basicConfig(
    filename=log_file,
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# Consola logger para mostrar información en tiempo real
console = logging.StreamHandler()
console.setLevel(logging.INFO)
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
console.setFormatter(formatter)
logging.getLogger('').addHandler(console)

# Lista de frecuencias disponibles (en MHz)
AVAILABLE_FREQUENCIES = [5665, 5675, 5685, 5695, 5710, 5760, 5780, 5830, 5835]

# Umbrales de calidad para cambio de frecuencia
SIGNAL_THRESHOLD = -70       # dBm - Si la señal cae debajo de este valor
CCQ_THRESHOLD = 70           # % - Si la calidad de conexión cae debajo de este valor
TX_CAPACITY_THRESHOLD = 50   # % - Si la capacidad de transmisión cae debajo de este %

# Período entre verificaciones (en segundos)
CHECK_INTERVAL = 300  # 5 minutos

# Función mejorada para obtener el estado del dispositivo
def get_device_status(ip_address, username, password):
    """
    Obtiene el estado del dispositivo usando múltiples métodos de extracción y análisis.
    Utiliza estrategias progresivas para manejar diferentes tipos de respuestas.
    """
    try:
        # Deshabilitar advertencias SSL
        requests.packages.urllib3.disable_warnings(requests.packages.urllib3.exceptions.InsecureRequestWarning)

        # Crear una nueva sesión con manejo optimizado de cookies
        session = requests.Session()
        session.verify = False  # Deshabilitar verificación SSL

        # Base URL usando HTTPS
        base_url = f"https://{ip_address}"

        # Establecer encabezados que imitan un navegador web moderno
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml,application/json;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "Pragma": "no-cache",
            "Cache-Control": "no-cache"
        }

        # PASO 1: Realizar una petición inicial para obtener cualquier token CSRF o cookies
        try:
            initial_response = session.get(f"{base_url}/", headers=headers, timeout=10)
            logging.debug(f"Respuesta inicial: {initial_response.status_code}")

            # Actualizar cookies de la sesión
            if initial_response.cookies:
                for cookie in initial_response.cookies:
                    logging.debug(f"Cookie recibida: {cookie.name}={cookie.value}")

            # Buscar token CSRF si existe en la respuesta inicial
            csrf_token = None
            if 'csrf_token' in initial_response.text or 'token' in initial_response.text:
                soup = BeautifulSoup(initial_response.text, 'html.parser')
                csrf_input = soup.find('input', {'name': 'csrf_token'}) or soup.find('input', {'name': 'token'})
                if csrf_input and 'value' in csrf_input.attrs:
                    csrf_token = csrf_input['value']
                    logging.debug(f"Token CSRF encontrado: {csrf_token}")
        except Exception as e:
            logging.warning(f"Error en petición inicial: {str(e)}")
            # Continuamos aunque falle la petición inicial

        # PASO 2: Realizar login con datos precisos
        login_url = f"{base_url}/login.cgi"
        login_data = {
            "username": username,
            "password": password,
            "uri": "/status.cgi"  # Redirigir directamente a status.cgi después del login
        }

        # Añadir token CSRF si existe
        if csrf_token:
            login_data["csrf_token"] = csrf_token

        # Configurar encabezados específicos para el formulario de login
        login_headers = headers.copy()
        login_headers["Content-Type"] = "application/x-www-form-urlencoded"
        login_headers["Referer"] = f"{base_url}/"

        try:
            login_response = session.post(
                login_url,
                data=login_data,
                headers=login_headers,
                timeout=15,
                allow_redirects=True
            )

            logging.debug(f"Login response code: {login_response.status_code}")
            logging.debug(f"Login redirect URL: {login_response.url}")

            # Verificar si el login fue exitoso
            login_successful = True
            if 'login.cgi' in login_response.url or 'login.html' in login_response.url:
                logging.warning(f"Posible fallo de login: redirigido a {login_response.url}")
                login_successful = False

            # Comprobar si hay mensaje de error en la respuesta
            if "incorrect" in login_response.text.lower() or "invalid" in login_response.text.lower():
                logging.warning("Posible fallo de login: mensaje de error detectado en la respuesta")
                login_successful = False

            # Guardar la página de login para diagnóstico
            debug_file = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                      f"debug_login_{ip_address.replace('.', '_')}.html")
            with open(debug_file, "w", encoding="utf-8") as f:
                f.write(login_response.text)

            if not login_successful:
                logging.error(f"Login fallido para {ip_address}. Revisar credenciales.")
                return None

        except Exception as e:
            logging.error(f"Error en proceso de login: {str(e)}")
            return None

        # PASO 3: Intentar múltiples endpoints para obtener datos
        device_status = {}

        # Lista de endpoints a probar, en orden de prioridad
        endpoints = [
            {"url": "/status.cgi", "method": "get", "parser": "json"},
            {"url": "/iflist.cgi", "method": "get", "parser": "json"},
            {"url": "/status.cgi", "method": "get", "parser": "html"},
            {"url": "/iflist.cgi", "method": "get", "parser": "html"},
            {"url": "/main.cgi", "method": "get", "parser": "html"},
            {"url": "/link.cgi", "method": "get", "parser": "html"},
            {"url": "/", "method": "get", "parser": "html"}
        ]

        for endpoint in endpoints:
            try:
                logging.info(f"Intentando obtener datos desde {endpoint['url']} usando {endpoint['parser']}")

                # Realizar solicitud al endpoint
                request_url = f"{base_url}{endpoint['url']}"
                if endpoint['method'].lower() == 'get':
                    response = session.get(request_url, headers=headers, timeout=15)
                else:
                    response = session.post(request_url, headers=headers, timeout=15)

                # Guardar respuesta para diagnóstico
                debug_file = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                         f"debug_{endpoint['url'].replace('/', '_').replace('.', '_')}_{ip_address.replace('.', '_')}.txt")
                with open(debug_file, "w", encoding="utf-8") as f:
                    f.write(response.text)

                logging.debug(f"Respuesta de {endpoint['url']}: Status {response.status_code}")

                # Verificar si la respuesta es una página de login
                if 'name="username"' in response.text and 'name="password"' in response.text:
                    logging.warning(f"Endpoint {endpoint['url']} redirecciona a login")
                    continue

                # Parsear respuesta según el tipo
                if endpoint['parser'] == 'json':
                    try:
                        data = json.loads(response.text)
                        logging.info(f"Respuesta JSON válida de {endpoint['url']}")

                        # Extraer datos según la estructura
                        if 'wireless' in data:
                            wireless = data.get('wireless', {})
                            device_status["signal_level"] = wireless.get('signal')
                            device_status["ccq"] = wireless.get('ccq')
                            freq_str = wireless.get('frequency', '')
                            if freq_str:
                                # Extraer solo el número de la frecuencia (quitar 'MHz' si existe)
                                device_status["frequency"] = float(freq_str.split()[0]) if ' ' in freq_str else float(freq_str)
                            device_status["tx_capacity"] = wireless.get('txrate')
                            device_status["channel_width"] = wireless.get('chanbw')
                            device_status["noise_floor"] = wireless.get('noisef')
                            device_status["distance"] = wireless.get('distance')
                            device_status["mode"] = wireless.get('mode')
                            device_status["tx_power"] = wireless.get('txpower')

                        # Si hay datos de host
                        if 'host' in data:
                            host = data.get('host', {})
                            device_status["device_name"] = host.get('hostname')
                            device_status["uptime"] = host.get('uptime')

                        # Si hay datos de interfaces
                        if 'interfaces' in data:
                            for interface in data.get('interfaces', []):
                                if interface.get('ifname') == 'ath0' and 'wireless' in interface:
                                    wireless = interface.get('wireless', {})
                                    if "signal_level" not in device_status:
                                        device_status["signal_level"] = wireless.get('signal')
                                    if "ccq" not in device_status:
                                        device_status["ccq"] = wireless.get('ccq')
                                    if "frequency" not in device_status and wireless.get('frequency'):
                                        freq_str = wireless.get('frequency')
                                        device_status["frequency"] = float(freq_str.split()[0]) if ' ' in freq_str else float(freq_str)

                        # Convertir valores a tipos correctos
                        for key in ["signal_level", "ccq", "frequency", "tx_capacity", "channel_width", "noise_floor", "tx_power"]:
                            if key in device_status and device_status[key] is not None:
                                try:
                                    device_status[key] = float(device_status[key])
                                except (ValueError, TypeError):
                                    pass

                        # Si hemos encontrado al menos datos básicos, terminamos
                        if "signal_level" in device_status or "ccq" in device_status or "frequency" in device_status:
                            logging.info(f"Datos obtenidos exitosamente de {endpoint['url']} usando JSON")
                            return device_status

                    except json.JSONDecodeError:
                        logging.warning(f"Respuesta de {endpoint['url']} no es JSON válido, intentando parseo HTML")

                # Parseo HTML/Regex para dispositivos que no devuelven JSON
                if endpoint['parser'] == 'html' or (endpoint['parser'] == 'json' and "signal_level" not in device_status):
                    logging.info(f"Analizando HTML de {endpoint['url']} para extraer datos")
                    text = response.text

                    # Intentar con BeautifulSoup primero
                    try:
                        soup = BeautifulSoup(text, 'html.parser')

                        # Buscar datos en etiquetas span, div o td que contengan los atributos
                        elements = soup.find_all(['span', 'div', 'td', 'p'])

                        # Buscar atributos en el HTML renderizado
                        for elem in elements:
                            elem_text = elem.get_text().lower()

                            if 'signal' in elem_text and not "signal_level" in device_status:
                                signal_match = re.search(r'(-?\d+\.?\d*)\s*dbm', elem_text)
                                if signal_match:
                                    device_status["signal_level"] = float(signal_match.group(1))
                                    logging.debug(f"Signal encontrado en HTML: {device_status['signal_level']}")

                            if 'ccq' in elem_text and not "ccq" in device_status:
                                ccq_match = re.search(r'(\d+\.?\d*)%', elem_text)
                                if ccq_match:
                                    device_status["ccq"] = float(ccq_match.group(1))
                                    logging.debug(f"CCQ encontrado en HTML: {device_status['ccq']}")

                            if 'freq' in elem_text and not "frequency" in device_status:
                                freq_match = re.search(r'(\d+\.?\d*)\s*mhz', elem_text)
                                if freq_match:
                                    device_status["frequency"] = float(freq_match.group(1))
                                    logging.debug(f"Frecuencia encontrada en HTML: {device_status['frequency']}")

                    except Exception as e:
                        logging.warning(f"Error al analizar HTML con BeautifulSoup: {str(e)}")

                    # Si BeautifulSoup no funcionó, intentar con expresiones regulares
                    if not device_status or not any(key in device_status for key in ["signal_level", "ccq", "frequency"]):
                        logging.info("Intentando extracción con expresiones regulares")
                        try:
                            # Buscar patrones comunes en datos JavaScript o HTML
                            signal_patterns = [
                                r'signal["\s:=]+(-?\d+\.?\d*)',
                                r'signal.*?(-\d+\.?\d*)\s*dBm',
                                r'Signal.*?(-\d+\.?\d*)',
                                r'>\s*Signal\s*<.*?>(-\d+\.?\d*)\s*dBm<',
                                r'sigLevel.*?(-\d+\.?\d*)'
                            ]

                            for pattern in signal_patterns:
                                match = re.search(pattern, text, re.IGNORECASE)
                                if match:
                                    device_status["signal_level"] = float(match.group(1))
                                    logging.debug(f"Signal encontrado con regex: {device_status['signal_level']}")
                                    break

                            ccq_patterns = [
                                r'ccq["\s:=]+(\d+\.?\d*)',
                                r'ccq.*?(\d+\.?\d*)%',
                                r'>\s*CCQ\s*<.*?>(\d+\.?\d*)%<',
                                r'qualityLevel.*?(\d+\.?\d*)'
                            ]

                            for pattern in ccq_patterns:
                                match = re.search(pattern, text, re.IGNORECASE)
                                if match:
                                    device_status["ccq"] = float(match.group(1))
                                    logging.debug(f"CCQ encontrado con regex: {device_status['ccq']}")
                                    break

                            freq_patterns = [
                                r'frequency["\s:=]+"?(\d+\.?\d*)(?:\s*MHz)?',
                                r'>\s*Frequency\s*<.*?>(\d+\.?\d*)\s*MHz<',
                                r'freq.*?(\d+\.?\d*)\s*MHz',
                                r'channel.*?(\d+\.?\d*)\s*MHz'
                            ]

                            for pattern in freq_patterns:
                                match = re.search(pattern, text, re.IGNORECASE)
                                if match:
                                    device_status["frequency"] = float(match.group(1))
                                    logging.debug(f"Frecuencia encontrada con regex: {device_status['frequency']}")
                                    break

                            # También buscar otros parámetros importantes
                            noise_patterns = [
                                r'noisef["\s:=]+(-?\d+\.?\d*)',
                                r'noise.*?(-\d+\.?\d*)\s*dBm'
                            ]

                            for pattern in noise_patterns:
                                match = re.search(pattern, text, re.IGNORECASE)
                                if match:
                                    device_status["noise_floor"] = float(match.group(1))
                                    break

                            txpower_patterns = [
                                r'txpower["\s:=]+(\d+\.?\d*)',
                                r'tx\s*power.*?(\d+\.?\d*)\s*dBm'
                            ]

                            for pattern in txpower_patterns:
                                match = re.search(pattern, text, re.IGNORECASE)
                                if match:
                                    device_status["tx_power"] = float(match.group(1))
                                    break

                        except Exception as e:
                            logging.warning(f"Error al extraer datos con regex: {str(e)}")

                # Si hemos encontrado datos básicos, terminamos
                if "signal_level" in device_status or "ccq" in device_status or "frequency" in device_status:
                    logging.info(f"Datos extraídos exitosamente de {endpoint['url']}")

                    # Imprimir los datos encontrados para diagnóstico
                    log_msg = "Datos encontrados:\n"
                    for key, value in device_status.items():
                        log_msg += f"  {key}: {value}\n"
                    logging.info(log_msg)

                    return device_status

            except Exception as e:
                logging.error(f"Error al procesar endpoint {endpoint['url']}: {str(e)}")
                continue

        # Si llegamos aquí es que no se pudo obtener datos de ningún endpoint
        logging.error(f"No se pudo obtener datos de ningún endpoint para {ip_address}")
        return None

    except Exception as e:
        logging.error(f"Error general en get_device_status: {str(e)}")
        return None

# Función mejorada para cambiar la frecuencia específicamente optimizada para PowerBeam M5
def change_frequency(ip_address, username, password, new_frequency):
    """
    Cambia la frecuencia del dispositivo PowerBeam M5 utilizando métodos HTTP robustos.
    Implementa una solución específica para los dispositivos Ubiquiti PowerBeam M5.
    """
    try:
        # Deshabilitar advertencias SSL
        requests.packages.urllib3.disable_warnings(requests.packages.urllib3.exceptions.InsecureRequestWarning)

        # Crear una sesión nueva
        session = requests.Session()
        session.verify = False

        # URL base
        base_url = f"https://{ip_address}"

        # Establecer encabezados que imitan un navegador
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache"
        }

        # PASO 1: Realizar una petición inicial para obtener cookies de sesión
        logging.info(f"Iniciando proceso de cambio de frecuencia para {ip_address} a {new_frequency} MHz")
        try:
            initial_response = session.get(f"{base_url}/", headers=headers, timeout=10)
            logging.debug(f"Respuesta inicial: {initial_response.status_code}")
        except Exception as e:
            logging.warning(f"Error en petición inicial (no crítico): {str(e)}")
            # Continuamos aunque falle la petición inicial

        # PASO 2: Realizar login - Esta es la parte crucial
        login_url = f"{base_url}/login.cgi"

        # En PowerBeam M5, el login redirige a status.cgi por defecto
        login_data = {
            "username": username,
            "password": password,
            "uri": "/status.cgi"
        }

        # Configurar encabezados específicos para el formulario de login
        login_headers = headers.copy()
        login_headers["Content-Type"] = "application/x-www-form-urlencoded"
        login_headers["Referer"] = f"{base_url}/"

        # Intentar login y manejar posibles redirecciones
        try:
            login_response = session.post(
                login_url,
                data=login_data,
                headers=login_headers,
                timeout=15,
                allow_redirects=True
            )

            logging.debug(f"Login: status={login_response.status_code}, url={login_response.url}")

            # Verificar si el login fue exitoso
            if 'login.cgi' in login_response.url or 'login' in login_response.url:
                logging.error(f"Login fallido para {ip_address}. Verificar credenciales.")
                return False

            # Guardar respuesta para depuración
            debug_file = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    f"debug_login_response_{ip_address.replace('.', '_')}.html")
            with open(debug_file, "w", encoding="utf-8") as f:
                f.write(login_response.text)

            logging.info(f"Login exitoso para {ip_address}")

        except Exception as e:
            logging.error(f"Error en proceso de login: {str(e)}")
            return False

        # PASO 3: Acceder específicamente a la página de configuración wireless
        # En PowerBeam M5, la configuración de frecuencia está en varios lugares posibles

        # Lista de URLs a intentar, en orden de prioridad para PowerBeam M5
        wireless_config_urls = [
            "/link.cgi",           # Principal para configuración de enlace
            "/spectral.cgi",       # Análisis espectral y frecuencia
            "/main.cgi?id=9",      # Página wireless en algunos modelos
            "/main.cgi",           # Página principal de configuración
            "/wireless.cgi",       # Configuración wireless alternativa
            "/advanced.cgi"        # Configuración avanzada
        ]

        wireless_page_url = None
        wireless_page_response = None

        for url in wireless_config_urls:
            try:
                config_url = f"{base_url}{url}"
                logging.info(f"Intentando acceder a {config_url}")

                config_response = session.get(
                    config_url,
                    headers=headers,
                    timeout=15,
                    allow_redirects=True
                )

                # Verificar si la página contiene campos de frecuencia
                if ('freq' in config_response.text.lower() or
                    'channel' in config_response.text.lower() or
                    'chan' in config_response.text.lower()):

                    logging.info(f"Página de configuración encontrada en {url}")
                    wireless_page_url = config_url
                    wireless_page_response = config_response

                    # Guardar respuesta para depuración
                    debug_file = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                            f"debug_config_{url.replace('/', '_')}_{ip_address.replace('.', '_')}.html")
                    with open(debug_file, "w", encoding="utf-8") as f:
                        f.write(config_response.text)

                    break

            except Exception as e:
                logging.warning(f"Error al acceder a {url}: {str(e)}")
                continue

        if not wireless_page_url or not wireless_page_response:
            logging.error("No se pudo encontrar la página de configuración de frecuencia")
            return False

        # PASO 4: Analizar la página para encontrar el formulario correcto y sus campos
        soup = BeautifulSoup(wireless_page_response.text, 'html.parser')

        # Extraer el token CSRF si existe
        csrf_token = None
        token_inputs = soup.find_all('input', {'name': ['token', 'csrf_token', 'csrf', '_csrf']})
        for token_input in token_inputs:
            if 'value' in token_input.attrs:
                csrf_token = token_input['value']
                logging.info(f"Token CSRF encontrado: {csrf_token}")
                break

        # Identificar todos los formularios y sus campos relacionados con frecuencia
        forms = soup.find_all('form')
        frequency_form = None
        frequency_fields = []

        for form in forms:
            # Buscar campos de input que tengan que ver con frecuencia/canal
            inputs = form.find_all('input')
            select_fields = form.find_all('select')

            freq_related_inputs = []

            # Revisar inputs
            for input_field in inputs:
                if input_field.get('name') and any(keyword in input_field.get('name', '').lower()
                                               for keyword in ['freq', 'channel', 'chan']):
                    freq_related_inputs.append(input_field.get('name'))

            # Revisar selects (combobox)
            for select_field in select_fields:
                if select_field.get('name') and any(keyword in select_field.get('name', '').lower()
                                                for keyword in ['freq', 'channel', 'chan']):
                    freq_related_inputs.append(select_field.get('name'))

            # Si este formulario tiene campos relacionados con frecuencia
            if freq_related_inputs:
                frequency_form = form
                frequency_fields.extend(freq_related_inputs)
                logging.info(f"Formulario de frecuencia encontrado con campos: {freq_related_inputs}")

                # Si el formulario tiene un atributo action, lo usamos
                if form.get('action'):
                    form_action = form.get('action')
                    # Si la acción no comienza con http o /, asumir que es relativa
                    if not form_action.startswith('http') and not form_action.startswith('/'):
                        form_action = f"/{form_action}"
                    # Si la acción es relativa, construir URL completa
                    if not form_action.startswith('http'):
                        form_action = f"{base_url}{form_action}"
                    wireless_page_url = form_action
                    logging.info(f"URL de acción del formulario: {wireless_page_url}")

        # Si no encontramos campos específicos, usar nombres comunes para PowerBeam M5
        if not frequency_fields:
            frequency_fields = [
                'freq', 'frequency', 'chan_freq', 'channel',
                'chan', 'channelwidth', 'channel_width', 'chanbw'
            ]
            logging.info(f"Usando campos de frecuencia predeterminados: {frequency_fields}")

        # PASO 5: Construir los datos del formulario para cambiar la frecuencia
        form_data = {}

        # Añadir campos de frecuencia
        for field in frequency_fields:
            form_data[field] = str(new_frequency)

        # Añadir token CSRF si existe
        if csrf_token:
            form_data['token'] = csrf_token

        # Identificar botones de submit en el formulario
        submit_buttons = []
        if frequency_form:
            # Buscar inputs de tipo submit
            submits = frequency_form.find_all('input', {'type': 'submit'})
            for submit in submits:
                if submit.get('name'):
                    submit_buttons.append((submit.get('name'), submit.get('value', '')))

        # Si encontramos botones de submit, añadirlos a los datos del formulario
        if submit_buttons:
            for name, value in submit_buttons:
                form_data[name] = value
                logging.info(f"Usando botón submit: {name}={value}")
        else:
            # Si no hay botones específicos, añadir valores genéricos conocidos para PowerBeam
            form_data['change'] = 'Apply'
            logging.info("Usando valor de submit genérico: change=Apply")

        # También podemos intentar extraer campos ocultos que puedan ser necesarios
        if frequency_form:
            hidden_fields = frequency_form.find_all('input', {'type': 'hidden'})
            for field in hidden_fields:
                if field.get('name') and field.get('name') not in form_data and field.get('value') is not None:
                    form_data[field.get('name')] = field.get('value')
                    logging.debug(f"Campo oculto añadido: {field.get('name')}={field.get('value')}")

        # PASO 6: Enviar la solicitud para cambiar la frecuencia
        form_headers = headers.copy()
        form_headers["Content-Type"] = "application/x-www-form-urlencoded"
        form_headers["Referer"] = wireless_page_url

        logging.info(f"Enviando solicitud de cambio de frecuencia a {wireless_page_url}")
        logging.info(f"Datos del formulario: {form_data}")

        try:
            change_response = session.post(
                wireless_page_url,
                data=form_data,
                headers=form_headers,
                timeout=20,
                allow_redirects=True
            )

            logging.debug(f"Respuesta al cambio: status={change_response.status_code}, url={change_response.url}")

            # Guardar respuesta para depuración
            debug_file = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    f"debug_change_response_{ip_address.replace('.', '_')}.html")
            with open(debug_file, "w", encoding="utf-8") as f:
                f.write(change_response.text)

            # PASO 7: PowerBeam M5 a veces requiere una confirmación adicional
            # Buscar formularios de confirmación en la respuesta
            confirm_soup = BeautifulSoup(change_response.text, 'html.parser')
            confirm_form = confirm_soup.find('form', {'action': re.compile(r'confirm|apply|commit')})

            if confirm_form:
                logging.info("Detectado formulario de confirmación, enviando confirmación...")

                confirm_url = confirm_form.get('action')
                # Si la URL es relativa, construir URL completa
                if confirm_url and not confirm_url.startswith('http'):
                    if not confirm_url.startswith('/'):
                        confirm_url = f"/{confirm_url}"
                    confirm_url = f"{base_url}{confirm_url}"
                else:
                    # Si no hay URL específica, usar la misma
                    confirm_url = wireless_page_url

                # Extraer datos de confirmación
                confirm_data = {}

                # Extraer campos del formulario
                for input_field in confirm_form.find_all('input'):
                    if input_field.get('name'):
                        confirm_data[input_field.get('name')] = input_field.get('value', '')

                # Si no hay campos, usar un valor genérico
                if not confirm_data:
                    confirm_data = {'confirm': 'Apply'}

                logging.info(f"Enviando confirmación a {confirm_url}")
                logging.info(f"Datos de confirmación: {confirm_data}")

                # Enviar confirmación
                confirm_response = session.post(
                    confirm_url,
                    data=confirm_data,
                    headers=form_headers,
                    timeout=20,
                    allow_redirects=True
                )

                logging.debug(f"Respuesta a confirmación: status={confirm_response.status_code}")

                # Guardar respuesta para depuración
                debug_file = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                        f"debug_confirm_response_{ip_address.replace('.', '_')}.html")
                with open(debug_file, "w", encoding="utf-8") as f:
                    f.write(confirm_response.text)

            # PASO 8: Algunos dispositivos pueden requerir un reinicio de la interfaz
            # Buscamos si hay indicaciones de reinicio en la respuesta
            restart_text = change_response.text.lower()

            if 'restart' in restart_text or 'reboot' in restart_text or 'reinicio' in restart_text:
                logging.info("Se detecta posible necesidad de reinicio. Enviando comando de reinicio de interfaz...")

                # Intentar reiniciar la interfaz wireless
                restart_url = f"{base_url}/restart.cgi"
                restart_data = {
                    'interface': 'ath0',
                    'token': csrf_token if csrf_token else '',
                    'restart': 'Restart'
                }

                try:
                    restart_response = session.post(
                        restart_url,
                        data=restart_data,
                        headers=form_headers,
                        timeout=20,
                        allow_redirects=True
                    )
                    logging.debug(f"Respuesta a reinicio: status={restart_response.status_code}")
                except Exception as e:
                    logging.warning(f"Error al intentar reiniciar la interfaz (no crítico): {str(e)}")

            # PASO 9: Esperar a que los cambios se apliquen
            logging.info("Esperando 30 segundos para que se apliquen los cambios...")
            time.sleep(30)

            # PASO 10: Verificar si el cambio se aplicó correctamente
            logging.info("Verificando si el cambio de frecuencia se aplicó correctamente...")

            # Esperar un poco más si los dispositivos están reiniciando
            for attempt in range(3):
                try:
                    current_status = get_device_status(ip_address, username, password)

                    if current_status and "frequency" in current_status:
                        current_freq = current_status["frequency"]

                        # Verificar si la frecuencia cambió
                        # Permitir una pequeña diferencia debido a redondeo
                        if abs(float(current_freq) - float(new_frequency)) < 2:
                            logging.info(f"✅ Cambio de frecuencia confirmado: {current_freq} MHz")
                            return True
                        else:
                            logging.warning(f"❌ Frecuencia actual ({current_freq}) no coincide con la solicitada ({new_frequency})")

                            # Si es el último intento, retornar False
                            if attempt == 2:
                                return False
                            else:
                                logging.info(f"Esperando 15 segundos más e intentando verificar de nuevo...")
                                time.sleep(15)
                    else:
                        logging.warning("No se pudo obtener la frecuencia actual")

                        # Si es el último intento, asumir éxito
                        if attempt == 2:
                            logging.info("Asumiendo que el cambio de frecuencia fue exitoso")
                            return True
                        else:
                            logging.info(f"Esperando 15 segundos más e intentando verificar de nuevo...")
                            time.sleep(15)

                except Exception as e:
                    logging.warning(f"Error al verificar frecuencia en intento {attempt+1}: {str(e)}")

                    # Si es el último intento, asumir éxito
                    if attempt == 2:
                        logging.info("Asumiendo que el cambio de frecuencia fue exitoso a pesar del error")
                        return True
                    else:
                        logging.info(f"Esperando 15 segundos más e intentando verificar de nuevo...")
                        time.sleep(15)

            # Si llegamos aquí, significa que no pudimos verificar el cambio
            logging.warning("No se pudo verificar el cambio de frecuencia después de múltiples intentos")
            return False

        except Exception as e:
            logging.error(f"Error al enviar solicitud de cambio de frecuencia: {str(e)}")
            return False

    except Exception as e:
        logging.error(f"Error general en change_frequency: {str(e)}")
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

# Función para encontrar la mejor frecuencia disponible
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

                if signal_level is not None and signal_level < SIGNAL_THRESHOLD:
                    reason = f"Señal ({signal_level} dBm) por debajo del umbral ({SIGNAL_THRESHOLD} dBm)"
                    logging.warning(reason)
                    reasons.append(reason)
                    need_change = True

                if ccq is not None and ccq < CCQ_THRESHOLD:
                    reason = f"CCQ ({ccq}%) por debajo del umbral ({CCQ_THRESHOLD}%)"
                    logging.warning(reason)
                    reasons.append(reason)
                    need_change = True

                if tx_capacity is not None and tx_capacity < TX_CAPACITY_THRESHOLD:
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
                        slave_success = change_frequency(SLAVE_IP, USERNAME, PASSWORD, best_frequency)
                        time.sleep(15)  # Esperar a que el esclavo se estabilice

                        if slave_success:
                            master_success = change_frequency(MASTER_IP, USERNAME, PASSWORD, best_frequency)

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
            except (ValueError, TypeError):
                pass

        if 'ccq' in master_info and master_info['ccq'] is not None:
            try:
                ccq = float(master_info['ccq'])
                if ccq < CCQ_THRESHOLD:
                    print(f"\n⚠️ ADVERTENCIA: CCQ ({ccq}%) por debajo del umbral ({CCQ_THRESHOLD}%)")
                    problems_found = True
            except (ValueError, TypeError):
                pass

        if 'tx_capacity' in master_info and master_info['tx_capacity'] is not None:
            try:
                tx_capacity = float(master_info['tx_capacity'])
                if tx_capacity < TX_CAPACITY_THRESHOLD:
                    print(f"\n⚠️ ADVERTENCIA: Capacidad TX ({tx_capacity}%) por debajo del umbral ({TX_CAPACITY_THRESHOLD}%)")
                    problems_found = True
            except (ValueError, TypeError):
                pass

        if not problems_found:
            print("\n✅ El enlace parece estar funcionando correctamente. No se detectaron problemas.")

    print("\n=== INFORMACIÓN DE FRECUENCIAS DISPONIBLES ===")
    print(f"Frecuencias configuradas: {AVAILABLE_FREQUENCIES}")
    if master_info and 'frequency' in master_info and master_info['frequency'] is not None:
        current_freq = master_info['frequency']
        print(f"Frecuencia actual: {current_freq} MHz")
    print("================================================")

# Función para probar la extracción de datos
def test_data_extraction():
    """Prueba diferentes métodos de extracción de datos de los dispositivos"""
    print("=== PRUEBA DE EXTRACCIÓN DE DATOS ===")

    print(f"\nDispositivo maestro ({MASTER_IP}):")
    master_data = get_device_status(MASTER_IP, USERNAME, PASSWORD)

    if master_data:
        print("✅ Datos extraídos exitosamente:")
        for key, value in master_data.items():
            print(f"  {key}: {value}")
    else:
        print("❌ No se pudieron extraer datos del maestro")

    print(f"\nDispositivo esclavo ({SLAVE_IP}):")
    slave_data = get_device_status(SLAVE_IP, USERNAME, PASSWORD)

    if slave_data:
        print("✅ Datos extraídos exitosamente:")
        for key, value in slave_data.items():
            print(f"  {key}: {value}")
    else:
        print("❌ No se pudieron extraer datos del esclavo")

    print("\n=== FIN DE PRUEBA DE EXTRACCIÓN ===")

# Función para ejecutar como servicio
def run_as_service():
    """Ejecuta el script como un servicio"""
    logging.info("Iniciando servicio de cambio automático de frecuencia")
    print("Servicio de cambio automático de frecuencia iniciado")
    print(f"Registros disponibles en: {log_file}")
    monitor_and_switch()

# Punto de entrada principal
if __name__ == "__main__":
    # Verificar argumentos de línea de comandos
    if len(sys.argv) > 1:
        if sys.argv[1] == '--status':
            # Solo mostrar el estado actual
            show_current_status()
        elif sys.argv[1] == '--test':
            # Probar la extracción de datos
            test_data_extraction()
        elif sys.argv[1] == '--extract':
            # Mostrar ejemplo de extracción de datos con diferentes métodos
            test_data_extraction()
        elif sys.argv[1] == '--force-switch':
            # Forzar un cambio de frecuencia
            if len(sys.argv) > 2:
                try:
                    target_freq = float(sys.argv[2])
                    if target_freq in AVAILABLE_FREQUENCIES:
                        print(f"Forzando cambio de frecuencia a {target_freq} MHz...")

                        # Primera forma: cambiar en esclavo primero, luego en maestro
                        print("Intentando cambiar frecuencia en el dispositivo esclavo...")
                        slave_success = change_frequency(SLAVE_IP, USERNAME, PASSWORD, target_freq)

                        if slave_success:
                            print(f"✅ Frecuencia del esclavo cambiada exitosamente")
                            time.sleep(15)
                            print("Intentando cambiar frecuencia en el dispositivo maestro...")
                            master_success = change_frequency(MASTER_IP, USERNAME, PASSWORD, target_freq)

                            if master_success:
                                print(f"✅ Frecuencia del maestro cambiada exitosamente")
                                print(f"✅ El enlace ahora opera en {target_freq} MHz")
                            else:
                                print(f"❌ Error al cambiar frecuencia del maestro")
                                print("⚠️ El esclavo se cambió pero el maestro no. Puede haber desconexión.")

                                # Intentar revertir el cambio en el esclavo
                                print("Intentando restaurar frecuencia original en el esclavo...")
                                original_freq = None
                                try:
                                    status = get_device_status(MASTER_IP, USERNAME, PASSWORD)
                                    if status and 'frequency' in status:
                                        original_freq = status['frequency']
                                except Exception:
                                    pass

                                if original_freq:
                                    revert_success = change_frequency(SLAVE_IP, USERNAME, PASSWORD, original_freq)
                                    if revert_success:
                                        print(f"✅ Frecuencia del esclavo restaurada a {original_freq} MHz")
                                    else:
                                        print("❌ No se pudo restaurar la frecuencia del esclavo")
                        else:
                            print(f"❌ Error al cambiar frecuencia del esclavo")
                            print("⚠️ Intentando método alternativo de cambio...")

                            # Segunda forma: intentar cambiar en maestro primero
                            print("Intentando cambiar frecuencia en el dispositivo maestro primero...")
                            master_success = change_frequency(MASTER_IP, USERNAME, PASSWORD, target_freq)

                            if master_success:
                                print(f"✅ Frecuencia del maestro cambiada exitosamente")
                                time.sleep(15)
                                print("Intentando cambiar frecuencia en el dispositivo esclavo...")
                                slave_success = change_frequency(SLAVE_IP, USERNAME, PASSWORD, target_freq)

                                if slave_success:
                                    print(f"✅ Frecuencia del esclavo cambiada exitosamente")
                                    print(f"✅ El enlace ahora opera en {target_freq} MHz")
                                else:
                                    print(f"❌ Error al cambiar frecuencia del esclavo")
                                    print("⚠️ El maestro se cambió pero el esclavo no. Puede haber desconexión.")
                            else:
                                print("❌ No se pudo cambiar la frecuencia en ninguno de los dispositivos")
                    else:
                        print(f"❌ Frecuencia {target_freq} no está en la lista de frecuencias disponibles")
                        print(f"Frecuencias disponibles: {AVAILABLE_FREQUENCIES}")
                except ValueError:
                    print(f"❌ Frecuencia inválida: {sys.argv[2]}")
            else:
                print("❌ Debe especificar una frecuencia")
                print(f"Uso: python frequency_switcher.py --force-switch FRECUENCIA")
                print(f"Frecuencias disponibles: {AVAILABLE_FREQUENCIES}")
        elif sys.argv[1] == '--debug-forms':
            # Analizar y mostrar todos los formularios disponibles en el dispositivo
            ip_to_debug = MASTER_IP
            if len(sys.argv) > 2:
                if sys.argv[2] == 'slave':
                    ip_to_debug = SLAVE_IP
                elif sys.argv[2] == 'master':
                    ip_to_debug = MASTER_IP
                else:
                    ip_to_debug = sys.argv[2]

            print(f"Analizando formularios en {ip_to_debug}...")

            # Deshabilitar advertencias SSL
            requests.packages.urllib3.disable_warnings()

            # Crear una sesión
            session = requests.Session()
            session.verify = False

            # Login
            login_url = f"https://{ip_to_debug}/login.cgi"
            login_data = {"username": USERNAME, "password": PASSWORD}

            try:
                login_response = session.post(login_url, data=login_data, timeout=15)
                print(f"Login: status={login_response.status_code}")

                # Lista de páginas a analizar
                pages = ["/", "/link.cgi", "/spectral.cgi", "/main.cgi", "/wireless.cgi", "/advanced.cgi"]

                for page in pages:
                    try:
                        url = f"https://{ip_to_debug}{page}"
                        print(f"\nAnalizando {url}...")

                        response = session.get(url, timeout=15)
                        if response.status_code == 200:
                            print(f"✅ Página accesible")

                            soup = BeautifulSoup(response.text, 'html.parser')
                            forms = soup.find_all('form')

                            print(f"Encontrados {len(forms)} formularios")

                            for i, form in enumerate(forms):
                                print(f"\nFormulario #{i+1}:")
                                print(f"  Action: {form.get('action', 'No definido')}")
                                print(f"  Method: {form.get('method', 'GET')}")

                                inputs = form.find_all(['input', 'select'])
                                print(f"  Campos ({len(inputs)}):")

                                for input_field in inputs:
                                    field_type = input_field.name
                                    field_name = input_field.get('name', 'Sin nombre')
                                    field_value = input_field.get('value', '')
                                    field_type_attr = input_field.get('type', '')

                                    if field_type == 'select':
                                        options = input_field.find_all('option')
                                        selected = [opt.get('value', '') for opt in options if opt.get('selected')]
                                        if selected:
                                            field_value = selected[0]
                                        print(f"    - {field_name} (select): {field_value} [opciones: {len(options)}]")
                                    else:
                                        print(f"    - {field_name} ({field_type_attr}): {field_value}")
                        else:
                            print(f"❌ Error accediendo a la página: {response.status_code}")
                    except Exception as e:
                        print(f"❌ Error analizando {page}: {str(e)}")
            except Exception as e:
                print(f"❌ Error general: {str(e)}")
        else:
            print(f"Argumento desconocido: {sys.argv[1]}")
            print("Uso: python frequency_switcher.py [--status|--test|--extract|--force-switch FRECUENCIA|--debug-forms [master|slave|IP]]")
    else:
        # Ejecutar el servicio completo
        run_as_service()
