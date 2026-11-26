# Simu-HL7-Lite

Demo independiente para practicar integraciones HL7 v2 por MLLP con un equipo
configurable, catálogo ampliable y resultados ingresados manualmente.

Conserva la apariencia y la navegación del simulador original: panel general,
operación de muestras, Worklist, mensajes, analizadores, configuración y perfil.
Permite configurar un solo equipo. Las opciones Premium permanecen en su lugar
habitual y al pulsarlas muestran un aviso; no ejecutan funciones Pro.
PACS y DICOM también están bloqueados mediante ese aviso.

```sh
git clone https://github.com/giomatamm20/Simu-HL7-Lite.git
cd Simu-HL7-Lite
```

## Iniciar con Docker

Desde esta carpeta, con Docker Compose instalado:

```sh
docker compose up --build -d
```

Abre [la aplicación](http://localhost:8091). Receptor MLLP: `localhost:5200`.
Los puertos publicados están vinculados a la propia computadora. El volumen
`lite-data` conserva la configuración, las órdenes y el registro de mensajes.

En **Configurar equipo**, configura el destino de los resultados. Para un receptor
en tu computadora desde Docker, usa `host.docker.internal` y su puerto MLLP.
`127.0.0.1` dentro del contenedor apunta al mismo contenedor.

## Iniciar con Python

Python 3.12 recomendado. Desde esta carpeta, en Windows:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn lite_app.main:app --host 127.0.0.1 --port 8091 --workers 1
```

En Linux/macOS:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m uvicorn lite_app.main:app --host 127.0.0.1 --port 8091 --workers 1
```

Variables opcionales: `LITE_DB_PATH` (por defecto `data/lite.db`),
`LITE_MLLP_HOST` (`127.0.0.1`) y `LITE_MLLP_PORT` (`5200`).
Usa un solo worker; cada instancia abre un listener MLLP.

## Primera prueba

1. Configura el destino en **Configurar equipo**: host y puerto de tu receptor.
2. En **Operar muestras**, crea una orden con datos ficticios y pruebas.
3. Abre la orden, ingresa cada valor numérico y pulsa **Preparar ORU**.
4. Revisa el mensaje y pulsa **Enviar por MLLP**.
5. Consulta el resultado y el ACK en **Mensajes**.

Con `AA` la orden queda aceptada. Con `AE`, `AR`, un ACK con identificador
incorrecto o un fallo de red conserva el ORU preparado y permite reintentar.
El reintento conserva el identificador de mensaje. Ante un corte de red, revisa
si el destino recibió el primer envío antes de repetirlo.

Puedes agregar y editar pruebas en **Perfil del equipo → Estudios y analitos**: código, nombre y
unidad. Se incluyen cinco ejemplos iniciales, sin límite de cantidad de pruebas
ni de selección por orden. No hay generadores automáticos. Los cambios del
catálogo y de la identidad del perfil se aplican al preparar un nuevo ORU;
un ORU preparado conserva su contenido. El host y puerto destino se leen al enviar.

### Recibir una orden

Envía `samples/order-glucose.hl7` al puerto `5200` con tu emisor MLLP, o desde
otra terminal Python, situada en esta carpeta:

```sh
python -c "from pathlib import Path; from lite_app.mllp import send; print(send('127.0.0.1', 5200, Path('samples/order-glucose.hl7').read_text()))"
```

La orden aparece pendiente; recibirla no genera resultados. Envía el mismo
mensaje nuevamente para comprobar que Lite devuelve el mismo ACK y no duplica
la orden. Para crear otra, cambia tanto `MSH-10` como `ORC-2` / `OBR-2`.

## Alcance Lite y Premium

| Función | Lite | Pro / Premium |
| --- | --- | --- |
| Envío y recepción MLLP | Funcional | Funcional |
| Visualización de HL7 y ACK | Funcional | Funcional |
| Validación de segmentos | Básica | Configurable |
| Resultados manuales | Funcional | Funcional |
| Perfil configurable | Uno | Múltiples equipos |
| Catálogo de pruebas | Editable, sin límite de cantidad | Configurable |
| Órdenes | ORM simple | Gestión avanzada |
| Resultados automáticos | Aviso Premium | Funcional |
| Análisis semántico HL7 | Aviso Premium | Funcional |
| Extracción de parámetros | Aviso Premium | Funcional |
| Plantillas personalizadas | Aviso Premium | Funcional |
| Múltiples equipos | Aviso Premium | Funcional |
| PACS y visor web | Aviso Premium | Funcional |
| Escenarios automatizados | Aviso Premium | Funcional |
| Importar / exportar perfiles | Aviso Premium | Funcional |

Los avisos Premium viven únicamente en la interfaz. Esta distribución no
incluye motores, endpoints ni dependencias Pro. No existe una variable para
activarlos. La columna Pro describe el alcance de la edición completa.

### Subconjunto HL7 de esta demo

- Separadores estándar `|^~\&`, texto ASCII o UTF-8 y ACK de aplicación `AA/AE/AR`.
- Recepción de `ORM^O01` con un `PID`, un `ORC` con `NW` y un `OBR` por mensaje.
  Requiere `PID-3`, un identificador consistente en `ORC-2` / `OBR-2` y un código
  de prueba de catálogo en `OBR-4.1`. `OBR-3` identifica la muestra, si existe.
- Recepción y visualización de `ORU^R01`, con respuesta ACK. Estos mensajes
  recibidos no crean órdenes ni generan resultados de salida.
- El generador manual crea `ORU^R01` con OBX numéricos `NM`, estado final `F`,
  procesamiento de prueba `T` y UTF-8 declarado. No calcula valores clínicos.
- **Mensaje manual** permite validar y enviar un ORM u ORU pegado en texto.
  No guarda plantillas ni propone configuraciones desde mensajes.
- Valida presencia de segmentos/campos básicos y números finitos; no ofrece
  conformidad completa con HL7 ni interpretación clínica o terminológica.
- Lectura fija de campos para registrar órdenes. Sin inferencia semántica,
  aprendizaje de perfiles, mapeos editables ni extracción de analitos.
- Bloques MLLP de hasta 256 KiB. Historial visible de los últimos 200 mensajes;
  conserva las órdenes y recibos para detectar repeticiones al reiniciar.

Demo para datos sintéticos y pruebas locales. No incluye autenticación ni TLS;
su configuración inicial mantiene los puertos en loopback.

## Pruebas

```sh
python -m pip install -r requirements-dev.txt
python -m unittest discover -s tests -v
```

Las pruebas usan bases temporales, sockets locales y pacientes ficticios.

## Preparar un repositorio público separado

```sh
python tools/package.py
```

Genera `dist/hl7-simulator-lite.zip`, un manifiesto SHA-256 y el checksum del ZIP.
Incluye solamente una lista explícita de archivos fuente, pruebas, configuración
Docker y ejemplos sintéticos. Excluye las bases, `.env`, archivos Pro y el
historial Git del proyecto original. Extrae el ZIP en una carpeta nueva para
iniciar el repositorio público; no publiques el repositorio completo como demo.

