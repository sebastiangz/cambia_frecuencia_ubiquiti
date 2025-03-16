#!/usr/bin/env python3
"""
Este script monitorea la calidad del enlace y cambia automáticamente la frecuencia
cuando detecta interferencia o degradación de la señal.
"""

import requests
import paramiko
import time
import logging
import json
import random
import subprocess
from datetime import datetime

# Configuración de logging
logging.basicConfig(
    filename='frequency_switcher.log',
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# Configuración de dispositivos
MASTER_IP = "192.168.40.17"  # Cambiar a la IP de tu radio maestro
SLAVE_IP = "192.168.40.18"   # Cambiar a la IP de tu radio esclavo
USERNAME = "ubnt"           # Usuario por defecto de Ubiquiti
PASSWORD = "lacontraseñaseguradeambosradios"       # Cambiar a tu contraseña

# Lista de frecuencias disponibles (en MHz)
AVAILABLE_FREQUENCIES = [5665, 5675, 5685, 5695, 5710, 5760, 5780, 5830, 5835]

# Umbrales de calidad para cambio de frecuencia
SIGNAL_THRESHOLD = -70       # dBm - Si la señal cae debajo de este valor
CCQ_THRESHOLD = 70           # % - Si la calidad de conexión cae debajo de este valor
TX_CAPACITY_THRESHOLD = 50   # % - Si la capacidad de transmisión cae debajo de este %

# Período entre verificaciones (en segundos)
CHECK_INTERVAL = 300  # 5 minutos

# Función para obtener el estado actual del enlace mediante SSH
def get_link_status_ssh(ip_address, username, password):
    """Obtiene métricas del enlace vía SSH"""
    try:
        # Establecer conexión SSH
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(ip_address, username=username, password=password, timeout=10)

        # Ejecutar comando para obtener estadísticas del enlace
        command = "mca-status"
        stdin, stdout, stderr = ssh.exec_command(command)
        output = stdout.read().decode('utf-8')
        ssh.close()

        # Parsear la salida
        signal_level = None
        ccq = None
        tx_capacity = None
        current_frequency = None

        for line in output.split('\n'):
            if "signal" in line.lower():
                try:
                    signal_level = int(line.split(':')[1].strip().split()[0])
                except:
                    pass
            elif "ccq" in line.lower():
                try:
                    ccq = float(line.split(':')[1].strip().split()[0])
                except:
                    pass
            elif "tx capacity" in line.lower():
                try:
                    tx_capacity = float(line.split(':')[1].strip().split()[0])
                except:
                    pass
            elif "frequency" in line.lower():
                try:
                    current_frequency = int(line.split(':')[1].strip().split()[0])
                except:
                    pass

        return {
            "signal_level": signal_level,
            "ccq": ccq,
            "tx_capacity": tx_capacity,
            "current_frequency": current_frequency
        }
    except Exception as e:
        logging.error(f"Error al obtener estado del enlace via SSH: {str(e)}")
        return None

# Función alternativa para obtener estado mediante la API HTTP
def get_link_status_api(ip_address, username, password):
    """Obtiene métricas del enlace vía API HTTP"""
    try:
        # URL para la API interna de AirOS
        url = f"http://{ip_address}/status.cgi"

        # Realizar la petición con autenticación básica
        response = requests.get(
            url,
            auth=(username, password),
            timeout=10
        )

        if response.status_code == 200:
            data = response.json()

            # Extraer información relevante
            wireless = data.get('wireless', {})
            return {
                "signal_level": wireless.get('signal'),
                "ccq": wireless.get('ccq'),
                "tx_capacity": wireless.get('txcapacity'),
                "current_frequency": wireless.get('frequency')
            }
        else:
            logging.error(f"Error HTTP {response.status_code} al obtener estado")
            return None
    except Exception as e:
        logging.error(f"Error al obtener estado del enlace via API: {str(e)}")
        return None

# Función para cambiar la frecuencia
def change_frequency(ip_address, username, password, new_frequency):
    """Cambia la frecuencia del dispositivo"""
    try:
        # Establecer conexión SSH
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(ip_address, username=username, password=password, timeout=10)

        # Comando para cambiar la frecuencia
        command = f"iwconfig ath0 freq {new_frequency/1000}G"
        stdin, stdout, stderr = ssh.exec_command(command)

        # Comando para guardar la configuración
        save_command = "cfgmtd -w -p /etc/"
        stdin, stdout, stderr = ssh.exec_command(save_command)

        # Reiniciar el servicio inalámbrico
        restart_command = "killall -HUP hostapd"
        stdin, stdout, stderr = ssh.exec_command(restart_command)

        ssh.close()
        return True
    except Exception as e:
        logging.error(f"Error al cambiar frecuencia: {str(e)}")
        return False

# Función alternativa para cambiar frecuencia mediante la API
def change_frequency_api(ip_address, username, password, new_frequency):
    """Cambia la frecuencia del dispositivo usando la API HTTP"""
    try:
        # URL para la API de configuración
        url = f"http://{ip_address}/cfg.cgi"

        # Datos para cambiar la frecuencia
        data = {
            "radio.1.freq": str(new_frequency),
            "commit": "1"
        }

        # Realizar la petición con autenticación básica
        response = requests.post(
            url,
            auth=(username, password),
            data=data,
            timeout=10
        )

        if response.status_code == 200:
            # Esperar a que se aplique el cambio
            time.sleep(5)
            return True
        else:
            logging.error(f"Error HTTP {response.status_code} al cambiar frecuencia")
            return False
    except Exception as e:
        logging.error(f"Error al cambiar frecuencia via API: {str(e)}")
        return False

# Función para escanear interferencias (usando el comando AirView si está disponible)
def scan_interference(ip_address, username, password):
    """Escanea el espectro para encontrar la frecuencia con menor interferencia"""
    try:
        # Establecer conexión SSH
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(ip_address, username=username, password=password, timeout=10)

        # Iniciar escaneo (ajustar según la versión de firmware)
        command = "ubntbox airview start"
        stdin, stdout, stderr = ssh.exec_command(command)

        # Esperar a que se complete el escaneo
        time.sleep(20)

        # Obtener resultados
        get_results = "cat /tmp/airview.fifo"  # Ajustar según la versión
        stdin, stdout, stderr = ssh.exec_command(get_results)
        output = stdout.read().decode('utf-8')

        # Detener escaneo
        stop_command = "ubntbox airview stop"
        stdin, stdout, stderr = ssh.exec_command(stop_command)

        ssh.close()

        # Analizar resultados para encontrar la mejor frecuencia
        # Esta es una implementación simplificada
        best_frequency = analyze_airview_results(output)
        return best_frequency
    except Exception as e:
        logging.error(f"Error en escaneo de interferencia: {str(e)}")
        return None

def analyze_airview_results(output):
    """Analiza los resultados de AirView para encontrar la mejor frecuencia"""
    # Implementación simplificada - en un escenario real se analizaría la salida de AirView
    # y se seleccionaría la frecuencia con menor nivel de ruido

    # Por defecto, usar una frecuencia aleatoria de la lista
    return random.choice(AVAILABLE_FREQUENCIES)

# Función principal para monitoreo continuo
def monitor_and_switch():
    """Monitorea la calidad del enlace y cambia la frecuencia si es necesario"""
    current_frequency = None
    consecutive_failures = 0

    while True:
        try:
            logging.info("Verificando estado del enlace...")

            # Obtener estado del enlace maestro
            master_status = get_link_status_ssh(MASTER_IP, USERNAME, PASSWORD)

            if not master_status:
                master_status = get_link_status_api(MASTER_IP, USERNAME, PASSWORD)

            if master_status:
                logging.info(f"Estado actual: Señal={master_status['signal_level']}dBm, "
                             f"CCQ={master_status['ccq']}%, Frecuencia={master_status['current_frequency']}MHz")

                current_frequency = master_status['current_frequency']

                # Verificar si se requiere cambio de frecuencia
                if (master_status['signal_level'] is not None and master_status['signal_level'] < SIGNAL_THRESHOLD) or \
                   (master_status['ccq'] is not None and master_status['ccq'] < CCQ_THRESHOLD) or \
                   (master_status['tx_capacity'] is not None and master_status['tx_capacity'] < TX_CAPACITY_THRESHOLD):

                    consecutive_failures += 1
                    logging.warning(f"Calidad del enlace por debajo del umbral ({consecutive_failures}/3)")

                    # Cambiar frecuencia después de 3 verificaciones fallidas consecutivas
                    if consecutive_failures >= 3:
                        logging.warning("Iniciando cambio de frecuencia...")

                        # Buscar la mejor frecuencia disponible
                        best_frequency = scan_interference(MASTER_IP, USERNAME, PASSWORD)

                        # Si no se puede escanear, elegir una frecuencia aleatoria diferente a la actual
                        if not best_frequency:
                            available_options = [f for f in AVAILABLE_FREQUENCIES if f != current_frequency]
                            best_frequency = random.choice(available_options)

                        logging.info(f"Cambiando frecuencia de {current_frequency}MHz a {best_frequency}MHz")

                        # Cambiar frecuencia en maestro y esclavo
                        master_success = change_frequency(MASTER_IP, USERNAME, PASSWORD, best_frequency)
                        time.sleep(5)  # Esperar antes de cambiar el esclavo
                        slave_success = change_frequency(SLAVE_IP, USERNAME, PASSWORD, best_frequency)

                        if master_success and slave_success:
                            logging.info("Cambio de frecuencia completado exitosamente")
                            consecutive_failures = 0
                        else:
                            logging.error("Error al cambiar la frecuencia")
                else:
                    # Enlace estable, reiniciar contador
                    if consecutive_failures > 0:
                        logging.info("Enlace estable, reiniciando contador de fallos")
                    consecutive_failures = 0
            else:
                logging.error("No se pudo obtener el estado del enlace")
                consecutive_failures += 1

            # Esperar antes de la próxima verificación
            time.sleep(CHECK_INTERVAL)

        except Exception as e:
            logging.error(f"Error en el ciclo de monitoreo: {str(e)}")
            time.sleep(60)  # Esperar un minuto antes de reintentar

# Función para correr como servicio
def run_as_service():
    """Ejecuta el script como un servicio"""
    logging.info("Iniciando servicio de cambio automático de frecuencia")
    monitor_and_switch()

if __name__ == "__main__":
    run_as_service()
