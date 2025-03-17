# Cambio Automático de Frecuencia para Ubiquiti PowerBeam

![Ubiquiti Logo](images/ub6053u2d5-ubiquiti-logo-ubiquiti-logo-.png)

## 📡 Descripción

Sistema automático para cambiar la frecuencia de los radios Ubiquiti PowerBeam cuando se detectan problemas de conectividad o interferencia. Esta solución permite que tus radios PtP (punto a punto) se adapten automáticamente a las mejores condiciones del espectro, similar a la funcionalidad que ofrecen los modelos LTU de Ubiquiti.

El sistema monitorea continuamente la calidad del enlace (señal, CCQ, capacidad de transmisión) y cambia automáticamente la frecuencia cuando se detecta una degradación, seleccionando la mejor frecuencia disponible con la menor interferencia.

## 🔍 Características

- **Monitoreo continuo** de la calidad del enlace de radio
- **Detección automática** de interferencias y degradación del enlace
- **Selección inteligente** de las mejores frecuencias disponibles
- **Cambio sincronizado** de frecuencias entre dispositivos maestro y esclavo
- **Extracción robusta de datos** utilizando múltiples métodos (JSON, HTML, regex)
- **Manejo avanzado de sesiones HTTP** para dispositivos con diferentes versiones de firmware
- **Interfaz de línea de comandos** con múltiples opciones y herramientas de diagnóstico
- **Logs detallados** para auditoría y solución de problemas

## ⚙️ Requisitos

- Python 3.6 o superior
- Dispositivos Ubiquiti PowerBeam M5 configurados en modo punto a punto (PtP)
- Acceso web a los dispositivos (usuario y contraseña)
- Dependencias Python: requests, beautifulsoup4

## 📋 Guía de Instalación

### 1. Clonar el repositorio

```bash
mkdir -p /home2/cambia_frecuencia_ubiquiti
cd /home2/cambia_frecuencia_ubiquiti
git clone https://github.com/sebastiangz/cambia_frecuencia_ubiquiti.git
# O si no usas git, simplemente crea los archivos manualmente
```

### 2. Instalar dependencias básicas

```bash
sudo dnf update
sudo dnf install -y python3 python3-pip python3-venv
```

### 3. Crear y activar un entorno virtual de Python

```bash
cd /home2/cambia_frecuencia_ubiquiti
python3 -m venv venv
source venv/bin/activate
```

### 4. Instalar dependencias en el entorno virtual

```bash
pip install requests beautifulsoup4
```

### 5. Configurar el script

Edita el archivo `frequency_switcher.py` para configurar tus dispositivos:

```bash
vi frequency_switcher.py
```

Modifica las siguientes variables según tu configuración:

```python
# Configuración de dispositivos
MASTER_IP = "192.168.1.20"  # Cambiar a la IP de tu radio maestro
SLAVE_IP = "192.168.1.21"   # Cambiar a la IP de tu radio esclavo
USERNAME = "ubnt"           # Usuario por defecto de Ubiquiti
PASSWORD = "password"       # Cambiar a tu contraseña

# Lista de frecuencias disponibles (en MHz)
AVAILABLE_FREQUENCIES = [5665, 5675, 5685, 5695, 5710, 5760, 5780, 5830, 5835]
```

### 6. Configurar el servicio systemd

Crea un archivo de servicio systemd para que el script se ejecute automáticamente:

```bash
sudo vi /etc/systemd/system/frequency-switcher.service
```

Copia y pega el siguiente contenido:

```
[Unit]
Description=Ubiquiti PowerBeam Automatic Frequency Switching Service
After=network.target

[Service]
User=root
WorkingDirectory=/home2/cambia_frecuencia_ubiquiti
ExecStart=/home2/cambia_frecuencia_ubiquiti/venv/bin/python /home2/cambia_frecuencia_ubiquiti/frequency_switcher.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

### 7. Habilitar e iniciar el servicio

```bash
sudo systemctl daemon-reload
sudo systemctl enable frequency-switcher.service
sudo systemctl start frequency-switcher.service
```

### 8. Verificar el funcionamiento

```bash
sudo systemctl status frequency-switcher.service
```

## 🛠️ Uso y opciones

El script proporciona varias opciones para diferentes escenarios:

### Ejecutar como servicio (modo continuo)

```bash
python frequency_switcher.py
```

### Mostrar estado actual

```bash
python frequency_switcher.py --status
```

### Probar extracción de datos

```bash
python frequency_switcher.py --extract
```

### Forzar cambio de frecuencia

```bash
python frequency_switcher.py --force-switch 5760
```

### Depurar formularios disponibles

```bash
python frequency_switcher.py --debug-forms master
```
o
```bash
python frequency_switcher.py --debug-forms slave
```

## 📐 Configuración personalizada

### Ajuste de parámetros de monitoreo

Puedes modificar los umbrales que determinan cuándo se debe cambiar la frecuencia:

```python
# Umbrales de calidad para cambio de frecuencia
SIGNAL_THRESHOLD = -70       # dBm - Si la señal cae debajo de este valor
CCQ_THRESHOLD = 70           # % - Si la calidad de conexión cae debajo de este valor
TX_CAPACITY_THRESHOLD = 50   # % - Si la capacidad de transmisión cae debajo de este %

# Período entre verificaciones (en segundos)
CHECK_INTERVAL = 300  # 5 minutos
```

## 📊 Monitoreo y logs

El script genera logs detallados que puedes revisar para monitorear su funcionamiento:

```bash
cat /home2/cambia_frecuencia_ubiquiti/frequency_switcher.log
```

También puedes verificar el estado del servicio con:

```bash
sudo systemctl status frequency-switcher.service
journalctl -u frequency-switcher.service
```

El script también genera archivos de depuración para diagnosticar problemas durante la extracción de datos y cambios de frecuencia. Estos archivos se guardan en el mismo directorio del script con nombres como:
- `debug_login_[IP].html`
- `debug_status.cgi_[IP].txt`
- `debug_config_[URL]_[IP].html`
- `debug_change_response_[IP].html`

## ❓ Solución de problemas

### El script no puede obtener información de los dispositivos

- Verifica que las direcciones IP sean correctas
- Comprueba que las credenciales sean válidas
- Utiliza el comando `--extract` para probar específicamente la extracción de datos
- Revisa los archivos de depuración generados en el directorio del script

### Error al cambiar la frecuencia

- Utiliza el comando `--debug-forms` para analizar los formularios disponibles en el dispositivo
- Revisa los logs y archivos de depuración para entender el problema
- Intenta con el método alternativo usando `--force-switch` que intenta diferentes estrategias

### No todos los datos se extraen correctamente

- El script implementa múltiples métodos de extracción (JSON, HTML, regex)
- Si algunos valores aparecen como "Desconocido", revisa los archivos de depuración generados
- Considera usar niveles de log más detallados para diagnóstico avanzado

### El servicio se detiene inesperadamente

- Revisa los logs del sistema con `journalctl -u frequency-switcher.service`
- Verifica que las dependencias de Python (requests, beautifulsoup4) estén correctamente instaladas

## 🔄 Funcionamiento interno

1. **Monitoreo**: El script verifica periódicamente la calidad del enlace (señal, CCQ, capacidad TX)
2. **Detección**: Si algún parámetro cae por debajo de los umbrales durante 3 verificaciones consecutivas, inicia el cambio
3. **Selección**: Elige una nueva frecuencia de la lista de frecuencias disponibles
4. **Cambio**: Primero cambia el esclavo, espera a que se estabilice, luego cambia el maestro
5. **Verificación**: Confirma que el cambio se aplicó correctamente en ambos dispositivos

El proceso de cambio de frecuencia sigue estos pasos:
1. Inicio de sesión en el dispositivo usando credenciales de acceso web
2. Identificación de la página correcta que contiene los controles de frecuencia
3. Análisis de los formularios y campos para encontrar los parámetros de frecuencia
4. Envío de la solicitud de cambio con los parámetros correctos
5. Manejo de posibles formularios de confirmación o reinicio de interfaz
6. Verificación del cambio exitoso

## 📝 Notas importantes

- El script está optimizado para PowerBeam M5 pero puede funcionar con otros modelos Ubiquiti
- Cada cambio de frecuencia genera una breve interrupción en el enlace (minimizada por la estrategia de cambio)
- Los archivos de depuración se acumulan en el directorio de ejecución y pueden requerir limpieza periódica
- El script no realiza un análisis espectral completo; selecciona frecuencias de una lista predefinida

## 🤝 Contribuir

Las contribuciones son bienvenidas! Puedes mejorar este proyecto de varias formas:

1. Reportando bugs
2. Sugiriendo mejoras
3. Enviando pull requests con nuevas características
4. Mejorando la documentación

## 📄 Licencia

Este proyecto está licenciado bajo la licencia MIT - consulta el archivo LICENSE para más detalles.

## 👥 Autor

Creado para facilitar los procesos WISP por sebastiangz

---

⭐ Si encuentras útil este proyecto, ¡considera darle una estrella en GitHub! ⭐
