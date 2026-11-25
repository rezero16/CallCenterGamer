import logging
import re
import unicodedata

from django.conf import settings

try:
    from google import genai
    from google.genai import types
except ImportError:  # pragma: no cover - permite usar el modo local sin la librería
    genai = None
    types = None


logger = logging.getLogger(__name__)

CATEGORIAS = [
    'hardware', 'software', 'conexion', 'cuenta',
    'pago', 'rendimiento', 'otros'
]

PALABRAS_POR_CATEGORIA = {
    'cuenta': [
        'cuenta', 'contrasena', 'clave', 'password', 'correo', 'email',
        'login', 'sesion', 'verificar', 'verificacion', 'baneo', 'ban',
        'suspension', 'recuperar', 'usuario',
    ],
    'pago': [
        'pago', 'pagar', 'tarjeta', 'cobro', 'reembolso', 'factura',
        'suscripcion', 'compra', 'precio', 'dinero', 'devolucion',
    ],
    'hardware': [
        'gpu', 'grafica', 'tarjeta', 'cpu', 'procesador', 'ram', 'memoria',
        'motherboard', 'placa', 'fuente', 'poder', 'ssd', 'disco', 'hdd',
        'ventilador', 'refrigeracion', 'teclado', 'mouse', 'monitor',
        'pantalla', 'audifonos', 'gabinete',
    ],
    'rendimiento': [
        'fps', 'rendimiento', 'lento', 'lentitud', 'tirones', 'lag',
        'temperatura', 'calentamiento', 'caliente', 'optimizar', 'fluidez',
        'congelado', 'congelamiento',
    ],
    'conexion': [
        'internet', 'wifi', 'red', 'ping', 'latencia', 'desconecta',
        'desconexion', 'servidor', 'conexion', 'online', 'ethernet',
        'router', 'modem', 'dns',
    ],
    'software': [
        'instalar', 'instalacion', 'actualizacion', 'actualizar', 'driver',
        'controlador', 'windows', 'steam', 'epic', 'juego', 'error', 'crash',
        'cierra', 'abre', 'ejecutable', 'launcher', 'antivirus', 'directx',
        'visual', 'c++', 'vcruntime', 'pantalla azul',
    ],
}

RESPUESTAS_LOCALES = {
    'hardware': (
        "Parece un tema de hardware. Prueba este orden:\n"
        "1. Revisa que todos los cables estén bien conectados y que la fuente tenga potencia suficiente.\n"
        "2. Vuelve a asentar la RAM y la tarjeta gráfica, limpiando contactos con cuidado.\n"
        "3. Verifica las temperaturas y que los ventiladores funcionen.\n"
        "4. Actualiza la BIOS solo si el fabricante lo recomienda y siguiendo sus instrucciones.\n\n"
        "Si me indicas el modelo de placa, CPU, GPU y el mensaje exacto, puedo orientarte mejor."
    ),
    'rendimiento': (
        "Para mejorar el rendimiento:\n"
        "1. Cierra programas en segundo plano y desactiva inicio automático innecesario.\n"
        "2. Actualiza los drivers de la GPU y el sistema operativo.\n"
        "3. Baja la calidad gráfica o resolución para ver si los FPS se estabilizan.\n"
        "4. Revisa temperaturas: si superan los 80-85 °C, puede haber throttling.\n"
        "5. En portátiles, asegúrate de usar la GPU dedicada y mantenerlo conectado a la corriente.\n\n"
        "Cuéntame qué juego, tu hardware y los FPS que obtienes para afinar el diagnóstico."
    ),
    'conexion': (
        "Para problemas de conexión:\n"
        "1. Reinicia el router y el equipo; espera 30 segundos antes de reconectar.\n"
        "2. Prueba con cable Ethernet para descartar el WiFi.\n"
        "3. Cierra VPN, proxies o programas que puedan interferir.\n"
        "4. Cambia temporalmente el DNS a 1.1.1.1 o 8.8.8.8.\n"
        "5. Verifica si el servidor del juego está caído o en mantenimiento.\n\n"
        "Dime si el problema es solo en un juego o en todo el equipo y qué ping/latencia ves."
    ),
    'cuenta': (
        "Para tu cuenta:\n"
        "1. Verifica que el usuario y el correo estén escritos correctamente.\n"
        "2. Usa la opción de recuperar contraseña y revisa spam/promociones.\n"
        "3. Si el problema es un baneo o suspensión, contacta al soporte oficial.\n"
        "4. No compartas tu contraseña ni códigos de verificación con nadie.\n\n"
        "Indícame qué mensaje exacto aparece al iniciar sesión y desde cuándo ocurre."
    ),
    'pago': (
        "Para temas de pago:\n"
        "1. Verifica los datos de la tarjeta y que tenga fondos disponibles.\n"
        "2. Revisa si el cobro quedó pendiente o duplicado en tu banco.\n"
        "3. Prueba otro método de pago o navegador.\n"
        "4. Guarda el comprobante y el ID de la transacción.\n\n"
        "Con esos datos puedo ayudarte a preparar el reclamo al soporte correspondiente."
    ),
    'software': (
        "Para un problema de juego o software:\n"
        "1. Verifica los requisitos mínimos del juego y el espacio libre en disco.\n"
        "2. Actualiza drivers, DirectX y Visual C++ Redistributable.\n"
        "3. Verifica la integridad de los archivos desde Steam/Epic u otro launcher.\n"
        "4. Ejecuta el juego como administrador y cierra overlays.\n"
        "5. Desactiva temporalmente el antivirus para probar, sin dejarlo desactivado.\n\n"
        "Cuéntame el nombre del juego, el mensaje de error exacto y qué pasa al abrirlo."
    ),
}

RESPUESTA_GENERAL = (
    "Puedo ayudarte con soporte de videojuegos y hardware. Para darte una respuesta útil, "
    "cuéntame:\n"
    "1. ¿Qué juego o programa presenta el problema?\n"
    "2. ¿Qué mensaje de error exacto aparece?\n"
    "3. ¿Desde cuándo ocurre y qué cambiaste antes?\n"
    "4. ¿Qué hardware y sistema operativo usas?\n\n"
    "Mientras tanto, prueba reiniciar el equipo y actualizar los drivers principales."
)

INSTRUCCIONES_SISTEMA = (
    "Eres el asistente técnico de Call Center Gamer. Responde siempre en español "
    "y sé breve: apunta a un máximo de 120 palabras, con pasos claros y concretos "
    "sobre videojuegos, hardware, software, conexión, cuentas y pagos. Usa el "
    "historial de la conversación para no repetir preguntas y adapta la respuesta "
    "al problema actual. Si faltan datos clave, pide solo la información necesaria "
    "para continuar. No inventes procedimientos, enlaces ni datos del usuario. No "
    "solicites contraseñas, códigos de verificación ni datos de pago sensibles. Si "
    "el problema requiere soporte humano, indica cómo escalarlo de forma segura."
)


class AsistenteIA:
    MAX_TOKENS_RESPUESTA = 300
    TIEMPO_LIMITE_MS = 2500
    TIEMPO_MINIMO_SERVIDOR_SEGUNDOS = 10
    NIVEL_RAZONAMIENTO = 'MINIMAL'

    def __init__(self):
        self.api_key = getattr(settings, 'GEMINI_API_KEY', '') or ''
        self.model = getattr(settings, 'GEMINI_MODEL', 'gemini-3.6-flash')
        self.timeout_ms = getattr(
            settings, 'GEMINI_TIMEOUT_MS', self.TIEMPO_LIMITE_MS
        )
        self._cliente = None

    def _client(self):
        if not self.api_key:
            raise RuntimeError("GEMINI_API_KEY no está configurada.")
        if genai is None or types is None:
            raise RuntimeError("La librería google-genai no está instalada.")
        if self._cliente is None:
            self._cliente = genai.Client(
                api_key=self.api_key,
                http_options=types.HttpOptions(
                    timeout=self.timeout_ms,
                    headers={
                        'X-Server-Timeout': str(
                            self.TIEMPO_MINIMO_SERVIDOR_SEGUNDOS
                        ),
                    },
                    retry_options=types.HttpRetryOptions(attempts=1),
                ),
            )
        return self._cliente

    def _normalizar(self, texto):
        texto = (texto or '').lower()
        texto = unicodedata.normalize('NFKD', texto)
        texto = ''.join(c for c in texto if not unicodedata.combining(c))
        return re.sub(r'[^a-z0-9\s+]', ' ', texto)

    def _clasificar_local(self, pregunta):
        texto = self._normalizar(pregunta)
        puntajes = {}
        for categoria, palabras in PALABRAS_POR_CATEGORIA.items():
            puntajes[categoria] = sum(
                1 for palabra in palabras
                if re.search(r'\b' + re.escape(palabra) + r'\b', texto)
            )

        mejor_categoria = max(puntajes, key=puntajes.get)
        return mejor_categoria if puntajes[mejor_categoria] > 0 else 'otros'

    def _respuesta_local(self, pregunta, historial_mensajes=None):
        texto = self._normalizar(pregunta)

        if re.search(r'\b(gracias|agradezco|muchas gracias)\b', texto):
            return "De nada. Si necesitas revisar otro problema de gaming o hardware, avísame."

        if (
            re.search(r'\b(hola|buenas|buenos dias|buenas tardes|buenas noches)\b', texto)
            and len(texto.split()) <= 5
        ):
            return (
                "Hola, soy el asistente de soporte de Call Center Gamer. "
                "Cuéntame qué problema tienes con tu juego, PC o cuenta y te ayudo a revisarlo."
            )

        categoria = self._clasificar_local(pregunta)
        if categoria in RESPUESTAS_LOCALES:
            return RESPUESTAS_LOCALES[categoria]
        return RESPUESTA_GENERAL

    def _historial_para_gemini(self, historial_mensajes):
        if not historial_mensajes:
            return []

        historial = []
        for item in historial_mensajes:
            contenido = (item.get('contenido') or '').strip()
            if not contenido:
                continue

            rol = 'model' if item.get('rol') == 'ia' else 'user'
            if historial and historial[-1].role == rol:
                contenido_anterior = historial[-1].parts[0].text or ''
                historial[-1] = types.Content(
                    role=rol,
                    parts=[types.Part.from_text(
                        text=f"{contenido_anterior}\n\n{contenido}"
                    )],
                )
            else:
                historial.append(types.Content(
                    role=rol,
                    parts=[types.Part.from_text(text=contenido)],
                ))

        while historial and historial[0].role != 'user':
            historial.pop(0)
        return historial

    def _instrucciones(self, historial_contexto, historial_mensajes):
        instrucciones = INSTRUCCIONES_SISTEMA
        contexto = (historial_contexto or '').strip()
        if contexto and not historial_mensajes:
            instrucciones = (
                f"{instrucciones}\n\nContexto adicional de la conversación:\n{contexto}"
            )
        return instrucciones

    def _config_gemini(self, historial_contexto, historial_mensajes):
        return types.GenerateContentConfig(
            system_instruction=self._instrucciones(
                historial_contexto, historial_mensajes
            ),
            max_output_tokens=self.MAX_TOKENS_RESPUESTA,
            thinking_config=types.ThinkingConfig(
                thinking_level=self.NIVEL_RAZONAMIENTO
            ),
        )

    def _crear_chat(self, historial_contexto, historial_mensajes):
        return self._client().chats.create(
            model=self.model,
            history=self._historial_para_gemini(historial_mensajes),
            config=self._config_gemini(
                historial_contexto, historial_mensajes
            ),
        )

    def _generar_con_gemini(self, pregunta, historial_contexto, historial_mensajes):
        chat = self._crear_chat(historial_contexto, historial_mensajes)
        response = chat.send_message(pregunta.strip())
        respuesta = (getattr(response, 'text', '') or '').strip()
        if not respuesta:
            raise RuntimeError("Gemini devolvió una respuesta vacía.")
        return respuesta

    def generar_respuesta_automatica(self, pregunta, historial_contexto="", historial_mensajes=None):
        """
        Genera una respuesta completa con Gemini y usa el modo local si no hay
        API key o si el servicio falla. Se usa en procesos que no necesitan
        streaming, como las sugerencias del panel de administración.
        """
        if not self.api_key:
            return self._respuesta_local(pregunta, historial_mensajes)

        try:
            return self._generar_con_gemini(
                pregunta, historial_contexto, historial_mensajes
            )
        except Exception:
            logger.exception("Error al consultar Gemini. Se usará el modo local.")
            return self._respuesta_local(pregunta, historial_mensajes)

    def generar_respuesta_stream(self, pregunta, historial_contexto="", historial_mensajes=None):
        """
        Entrega la respuesta de Gemini por fragmentos. Si no hay API key o el
        servicio falla antes de emitir texto, entrega la respuesta local para
        que el usuario reciba algo de inmediato.
        """
        if not self.api_key:
            yield self._respuesta_local(pregunta, historial_mensajes)
            return

        hubo_texto = False
        try:
            chat = self._crear_chat(historial_contexto, historial_mensajes)
            for response in chat.send_message_stream(pregunta.strip()):
                texto = getattr(response, 'text', '') or ''
                if texto:
                    hubo_texto = True
                    yield texto

            if not hubo_texto:
                raise RuntimeError("Gemini devolvió una respuesta vacía.")
        except Exception:
            if hubo_texto:
                raise
            logger.exception(
                "Error al transmitir Gemini. Se usará el modo local."
            )
            yield self._respuesta_local(pregunta, historial_mensajes)

    def clasificar_pregunta(self, pregunta):
        """
        Clasifica la pregunta usando reglas locales para evitar latencia y
        consumo innecesario de la API de Gemini.
        """
        return self._clasificar_local(pregunta)
