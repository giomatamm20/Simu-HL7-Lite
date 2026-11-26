# Simu-HL7-Lite

Demo independiente para practicar integraciones HL7 v2 por MLLP con **un equipo
y un diseño completo de resultados**, ambos configurables. Incluye catálogo
sin límite de analitos y resultados ingresados manualmente.

Conserva la apariencia y la navegación del simulador original: panel general,
operación de muestras, Worklist, mensajes, analizadores, configuración y perfil.
Permite configurar un solo equipo y guardar un único diseño editable. Las opciones Premium permanecen en su lugar
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
2. En **Perfil del equipo → Campos del mensaje**, revisa los segmentos, sus
   campos/componentes y el origen de cada valor. La vista previa usa datos ficticios.
3. Pulsa **Guardar y activar diseño**. Sin un diseño activado el equipo queda
   en **STANDBY**; con el diseño válido y sin órdenes pendientes queda en **IDLE**.
4. En **Operar muestras**, crea una orden con datos ficticios, ID de muestra,
   código/nombre de estudio y pruebas. También puedes recibir una orden por MLLP.
5. Carga la muestra o abre la orden en Worklist, ingresa sus valores y pulsa
   **Preparar ORU**. El diseño activo arma el mensaje de salida real.
6. Revisa el mensaje, pulsa **Enviar por MLLP** y consulta su ACK en **Mensajes**.

Con `AA` la orden queda aceptada. Con `AE`, `AR`, un ACK con identificador
incorrecto o un fallo de red conserva el ORU preparado y permite reintentar.
El reintento conserva el identificador de mensaje. Ante un corte de red, revisa
si el destino recibió el primer envío antes de repetirlo.

Puedes agregar y editar analitos en **Perfil del equipo → Estudios y analitos**:
código, nombre, tipo numérico `NM` o texto `ST`/`TX`, unidad, referencia y bandera
manual. Se incluyen cinco ejemplos iniciales, sin límite de cantidad ni de
selección por orden. No hay generadores automáticos para las órdenes.

Si cambias los valores visibles, debes preparar nuevamente el ORU antes de
enviarlo. Cambiar un analito usado, la identidad HL7 o el diseño invalida la
preparación anterior de las órdenes pendientes; los mensajes ya aceptados
conservan su contenido. El host y puerto destino se leen al enviar.

### Armar el único mensaje completo

En **Campos del mensaje** puedes añadir, quitar y ordenar segmentos; editar
campos (`3`) y componentes (`3.1`); y elegir un valor fijo o un origen explícito
de paciente, orden, muestra, estudio, equipo, fecha o analito. Los segmentos
marcados **por cada analito** se repiten. Se admiten segmentos adicionales como
`NTE` o segmentos locales `Zxx`. El diseño de salida es `ORU^R01`, con `MSH`,
`PID`, `OBR` y `OBX`, y separadores estándar. `MSH-10` debe usar un ID único.

La vista previa permite elegir un analito, los primeros tres o todo el catálogo.
Muestra los segmentos y la relación **campo → origen → valor del ejemplo**.
Los ejemplos no crean resultados de órdenes ni envían mensajes.

**Guardar y activar** reemplaza el único diseño existente. No guarda copias ni
una biblioteca; importar, exportar, guardar diseños adicionales y el diseñador
semántico permanecen Premium. Un borrador inválido no sustituye el diseño activo.
Cambiar la selección de segmentos o de analitos en la vista previa tampoco
modifica el diseño guardado.

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
| Resultados manuales | Numéricos y texto | Funcional |
| Perfil configurable | Uno | Múltiples equipos |
| Mensaje de resultados | Un diseño manual completo editable | Múltiples diseños |
| Segmentos, campos y componentes | Editor manual y vista previa | Editor e inferencia semántica |
| Catálogo de pruebas | Editable, sin límite de cantidad | Configurable |
| Órdenes | ORM simple | Gestión avanzada |
| Resultados automáticos | Aviso Premium | Funcional |
| Análisis semántico HL7 | Aviso Premium | Funcional |
| Extracción de parámetros | Aviso Premium | Funcional |
| Guardar diseños adicionales | Aviso Premium | Funcional |
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
- El diseño manual arma `ORU^R01` con OBX `NM`/`ST`/`TX`. La base incluida usa
  estado final `F`, procesamiento de prueba `T` y UTF-8 declarado. No calcula
  valores clínicos; los valores y banderas de las órdenes son manuales.
- **Mensaje manual** permite validar y enviar un ORM u ORU pegado en texto.
  No guarda plantillas ni propone configuraciones desde mensajes.
- Valida presencia de segmentos/campos básicos y números finitos; no ofrece
  conformidad completa con HL7 ni interpretación clínica o terminológica.
- Lectura fija de campos para registrar órdenes. Los mapeos de salida se editan
  manualmente en el único diseño; sin inferencia semántica, aprendizaje de
  perfiles ni extracción automática de analitos.
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

