# Cambio Automático de Frecuencia para Ubiquiti PowerBeam

![Ubiquiti Logo](https://i.imgur.com/4xN5O8J.png)

## 📡 Descripción

Sistema automático para cambiar la frecuencia de los radios Ubiquiti PowerBeam cuando se detectan problemas de conectividad o interferencia. Esta solución permite que tus radios PtP (punto a punto) se adapten automáticamente a las mejores condiciones del espectro, similar a la funcionalidad que ofrecen los modelos LTU de Ubiquiti.

El sistema monitorea continuamente la calidad del enlace (señal, CCQ, capacidad de transmisión) y cambia automáticamente la frecuencia cuando se detecta una degradación, seleccionando la mejor frecuencia disponible con la menor interferencia.

## 🔍 Características

- **Monitoreo continuo** de la calidad del enlace de radio
- **Detección automática** de interferencias y degradación del enlace
- **Selección inteligente** de las mejores frecuencias disponibles
- **Escaneo del espectro** mediante AirView (cuando está disponible)
- **Cambio sincronizado** de frecuencias entre dispositivos maestro y esclavo
- **Funcionamiento como servicio** que se ejecuta automáticamente en el arranque
- **Logs detallados** para auditoría y solución de problemas

## ⚙️ Requisitos

- Python 3.6 o superior
- Acceso SSH a los radios Ubiquiti PowerBeam
- Credenciales de administrador para los dispositivos
- Radios PowerBeam M5 400 (u otros modelos compatibles de Ubiquiti)

## 📋 Guía de Instalación

### 1. Clonar el repositorio

```bash
mkdir -p /home2/cambia_frecuencia_ubiquiti
cd /home2/cambia_frecuencia_ubiquiti
git clone [[https://github.com/tu_usuario/cambia_frecuencia_ubiquiti](https://github.com/sebastiangz/cambia_frecuencia_ubiquiti)](https://github.com/sebastiangz/cambia_frecuencia_ubiquiti/).git .
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
pip install paramiko requests
```

### 5. Configurar el script

Edita el archivo `frequency_switcher.py` para configurar tus dispositivos:

```bash
nano frequency_switcher.py
```

Modifica las siguientes variables según tu configuración:

```python
# Configuración de dispositivos
MASTER_IP = "192.168.1.20"  # Cambiar a la IP de tu radio maestro
SLAVE_IP = "192.168.1.21"   # Cambiar a la IP de tu radio esclavo
USERNAME = "ubnt"           # Usuario por defecto de Ubiquiti
PASSWORD = "password"       # Cambiar a tu contraseña

# Lista de frecuencias disponibles o que tú ulizas en elos radios PtP (en MHz)
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

## 🛠️ Configuración personalizada

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

### Integración con UISP/UNMS - Extra y para TODO

Si utilizas UISP (antes conocido como UNMS) para gestionar tus dispositivos Ubiquiti, puedes modificar el script para usar la API de UISP:

1. Instala la biblioteca para la API de UISP (dentro del entorno virtual):
   ```bash
   source /home2/cambia_frecuencia_ubiquiti/venv/bin/activate
   pip install uisp-client
   ```

2. Modifica el script para utilizar las funciones de la API de UISP.

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

## ❓ Solución de problemas

### 1. El script no puede conectarse a los radios

- Verifica que las direcciones IP sean correctas
- Comprueba que las credenciales sean válidas
- Asegúrate de que haya conectividad de red con los radios

### 2. Los cambios de frecuencia no persisten

- Verifica que el comando para guardar la configuración (`cfgmtd -w -p /etc/`) funcione en tus dispositivos
- Comprueba los logs para ver si hay errores

### 3. El servicio se detiene inesperadamente

- Revisa los logs del sistema con `journalctl -u frequency-switcher.service`
- Verifica que las dependencias de Python estén correctamente instaladas

### 4. Problemas con el entorno virtual

- Verifica que la ruta al intérprete de Python sea correcta en el archivo de servicio
- Comprueba que todas las dependencias estén instaladas en el entorno virtual:
  ```bash
  source /home2/cambia_frecuencia_ubiquiti/venv/bin/activate
  pip list | grep paramiko
  pip list | grep requests
  ```
- Si es necesario, reinstala las dependencias:
  ```bash
  source /home2/cambia_frecuencia_ubiquiti/venv/bin/activate
  pip install --upgrade paramiko requests
  ```

## 🔄 Actualización

Para actualizar el script en el futuro:

```bash
cd /home2/cambia_frecuencia_ubiquiti
source venv/bin/activate
# Edita el archivo según sea necesario
nano frequency_switcher.py
# Reinicia el servicio
sudo systemctl restart frequency-switcher.service
```

## 📝 Notas importantes

- Este script depende del acceso SSH a los dispositivos Ubiquiti, asegúrate de que el SSH esté habilitado en los radios
- Los comandos pueden variar ligeramente según la versión del firmware de tus dispositivos
- Para un funcionamiento óptimo, configura una lista limitada de frecuencias que sepas que funcionan bien en tu entorno

## 🤝 Contribuir

Las contribuciones son bienvenidas! Puedes mejorar este proyecto de varias formas:

1. Reportando bugs
2. Sugiriendo mejoras
3. Enviando pull requests con nuevas características
4. Mejorando la documentación

## 📄 Licencia

Este proyecto está licenciado bajo la licencia MIT - consulta el archivo LICENSE para más detalles.

## 👥 Autor

Creado originalmente por [Tu Nombre] - [Tu sitio web o perfil de GitHub]

---

⭐ Si encuentras útil este proyecto, ¡considera darle una estrella en GitHub! ⭐
