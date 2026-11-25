import json
import logging

from django.shortcuts import render, redirect
from django.http import JsonResponse, StreamingHttpResponse
from django.contrib.auth import logout
from django.utils import timezone
from django.views.decorators.http import require_POST
from online.forms import RegisterForm, LoginForm, PreguntasForm, RespuestaForm
from django.views.generic import CreateView, FormView
from django.contrib.auth import authenticate, login
from django.contrib.auth.decorators import login_required
from online.models import (
    Preguntas, Usuario, Respuesta, ConversacionIA, MensajeIA
)
from online.ia_utils import AsistenteIA

logger = logging.getLogger(__name__)

# Vista para la página principal
def Index(request):
    """
    Página principal del sitio
    """
    return render(request, "index.html")

# Vista para logout personalizada
@require_POST
def logout_view(request):
    """
    Vista personalizada para logout
    """
    logout(request)
    return redirect('Login')

# Vista de chat con IA
@login_required
@require_POST
def ChatIA(request, id):
    """
    Endpoint AJAX del asistente de IA conversacional.

    Las conversaciones de IA se guardan en ConversacionIA/MensajeIA y nunca
    en las tablas de consultas (Preguntas/Respuesta).
    """
    user = request.user

    if request.user.id != id:
        return JsonResponse({
            'exito': False,
            'error': 'No puedes acceder al chat de otro usuario.'
        }, status=403)

    mensaje = request.POST.get("mensaje", "").strip()
    if not mensaje:
        return JsonResponse({
            'exito': False,
            'error': 'Escribe un mensaje antes de enviar.'
        }, status=400)

    if len(mensaje) > 2000:
        return JsonResponse({
            'exito': False,
            'error': 'El mensaje es demasiado largo (máximo 2000 caracteres).'
        }, status=400)

    conversacion = None
    conversacion_id = request.POST.get("conversacion_id", "").strip()
    if conversacion_id.isdigit():
        conversacion = ConversacionIA.objects.filter(
            idConversacion=int(conversacion_id),
            Usuario=user
        ).first()

    if conversacion is None:
        titulo = " ".join(mensaje.split())[:60] or "Nueva conversación"
        conversacion = ConversacionIA.objects.create(
            Usuario=user,
            Titulo=titulo
        )

    try:
        historial_mensajes = obtener_historial_ia(conversacion)

        MensajeIA.objects.create(
            Conversacion=conversacion,
            Rol=MensajeIA.ROL_USUARIO,
            Contenido=mensaje
        )

        acepta_stream = 'text/event-stream' in request.headers.get('Accept', '')
        if acepta_stream:
            return respuesta_stream_ia(
                conversacion, mensaje, historial_mensajes
            )

        ia = AsistenteIA()
        respuesta_ia = ia.generar_respuesta_automatica(
            mensaje,
            historial_mensajes=historial_mensajes
        )

        MensajeIA.objects.create(
            Conversacion=conversacion,
            Rol=MensajeIA.ROL_IA,
            Contenido=respuesta_ia
        )
        conversacion.save(update_fields=['Fecha_actualizacion'])

        return JsonResponse({
            'exito': True,
            'conversacion_id': conversacion.idConversacion,
            'titulo': conversacion.Titulo,
            'pregunta': mensaje,
            'respuesta': respuesta_ia,
            'usuario': user.usuario
        })

    except Exception:
        logger.exception("Error procesando el mensaje de IA")
        return JsonResponse({
            'exito': False,
            'error': 'No se pudo procesar el mensaje. Intenta nuevamente.'
        }, status=500)


def evento_sse(nombre, datos):
    """Formatea un evento SSE para respuestas en streaming."""
    return (
        f"event: {nombre}\n"
        f"data: {json.dumps(datos, ensure_ascii=False)}\n\n"
    )


def respuesta_stream_ia(conversacion, mensaje, historial_mensajes):
    """
    Devuelve la respuesta de la IA mediante Server-Sent Events y guarda el
    mensaje completo al finalizar.
    """
    def generar_eventos():
        partes = []
        try:
            yield evento_sse('meta', {
                'conversacion_id': conversacion.idConversacion,
                'titulo': conversacion.Titulo,
            })

            asistente = AsistenteIA()
            for fragmento in asistente.generar_respuesta_stream(
                mensaje,
                historial_mensajes=historial_mensajes,
            ):
                texto = fragmento or ''
                if not texto:
                    continue
                partes.append(texto)
                yield evento_sse('delta', {'texto': texto})

            respuesta_ia = ''.join(partes).strip()
            if not respuesta_ia:
                raise RuntimeError("La IA devolvió una respuesta vacía.")

            MensajeIA.objects.create(
                Conversacion=conversacion,
                Rol=MensajeIA.ROL_IA,
                Contenido=respuesta_ia,
            )
            conversacion.save(update_fields=['Fecha_actualizacion'])

            yield evento_sse('done', {
                'conversacion_id': conversacion.idConversacion,
                'titulo': conversacion.Titulo,
                'respuesta': respuesta_ia,
            })
        except Exception:
            logger.exception("Error durante el streaming de IA")
            yield evento_sse('error', {
                'error': 'No se pudo completar la respuesta. Intenta nuevamente.'
            })

    respuesta = StreamingHttpResponse(
        generar_eventos(),
        content_type='text/event-stream',
    )
    respuesta['Cache-Control'] = 'no-cache'
    respuesta['X-Accel-Buffering'] = 'no'
    return respuesta


def obtener_historial_ia(conversacion, limite=6):
    """
    Devuelve los últimos mensajes de una conversación de IA, en orden
    cronológico, para usarlos como contexto. No lee consultas normales.
    """
    mensajes = conversacion.mensajes.order_by(
        '-FechaHora', '-idMensaje'
    )[:limite]
    return [
        {'rol': mensaje.Rol, 'contenido': mensaje.Contenido}
        for mensaje in reversed(list(mensajes))
    ]

# Vista de inicio de usuario
@login_required
def Inicio(request, id):
    """
    Vista del panel de usuario
    """
    try:
        User = Usuario.objects.get(id=id)
        if request.user.id != User.id:
            return redirect('/Inicio/' + str(request.user.id) + '/')
        preguntas = Preguntas.objects.filter(Usuario=User).order_by('-Fecha_creacion')
        now = timezone.now()
        form = PreguntasForm(initial={'Usuario': id, 'Fecha_creacion': now})

        es_formulario_pregunta = (
            request.method == "POST"
            and 'Titulo' in request.POST
            and 'Pregunta' in request.POST
        )
        if es_formulario_pregunta:
            form = PreguntasForm(request.POST)
            if form.is_valid():
                pregunta = form.save(commit=False)
                pregunta.Usuario = User
                pregunta.Fecha_creacion = now
                pregunta.save()
                return redirect('/Inicio/' + str(id) + '/')

        conversaciones_ia = ConversacionIA.objects.filter(Usuario=User)
        conversacion_actual = None

        if request.GET.get('nueva') != '1':
            conversacion_id = request.GET.get('conversacion', '').strip()
            if conversacion_id.isdigit():
                conversacion_actual = conversaciones_ia.filter(
                    idConversacion=int(conversacion_id)
                ).first()
            if conversacion_actual is None:
                conversacion_actual = conversaciones_ia.first()

        if conversacion_actual is not None:
            mensajes_ia = conversacion_actual.mensajes.all()
        else:
            mensajes_ia = MensajeIA.objects.none()

        return render(request, "inicio.html", {
            "Preguntas": preguntas,
            "form": form,
            "id": id,
            "conversaciones_ia": conversaciones_ia,
            "conversacion_actual": conversacion_actual,
            "mensajes_ia": mensajes_ia,
        })
    except Usuario.DoesNotExist:
        return redirect('/')

# Vista de respuestas
@login_required
def ViewRespuestas(request, idUser, idPregunta):
    """
    Vista para ver y responder preguntas
    """
    try:
        pregunta = Preguntas.objects.get(idPregunta=idPregunta)
        if not (
            request.user.id == pregunta.Usuario_id
            or request.user.admin
            or request.user.tecnico
        ):
            return redirect('/Inicio/' + str(request.user.id) + '/')

        now = timezone.now()
        MostrarRespuestas = Respuesta.objects.filter(id_Pregunta=pregunta)

        form = RespuestaForm(initial={
            'id_Pregunta': idPregunta,
            'Usuario': request.user.id,
            'FechaHora': now
        })

        if request.method == "POST":
            form = RespuestaForm(request.POST)
            if form.is_valid():
                respuesta = form.save(commit=False)
                respuesta.id_Pregunta = pregunta
                respuesta.Usuario = request.user
                respuesta.FechaHora = now
                respuesta.save()
                return redirect('/Respuesta/' + str(idUser) + "/" + str(idPregunta) + '/')

        return render(request, "Respuestas.html", {
            "Pregunta": pregunta,
            "form": form,
            "Respuestas": MostrarRespuestas
        })
    except Preguntas.DoesNotExist:
        return redirect('/')

class RegisterView(CreateView):
    form_class = RegisterForm
    template_name = 'register.html'
    success_url = '/'

    def form_valid(self, form):
        request = self.request
        user = form.save()
        login(request, user)
        if user.admin:
            return redirect('/Administracion/')
        return redirect('/Inicio/' + str(user.id) + '/')

class LoginView(FormView):
    form_class = LoginForm
    template_name = 'login.html'
    success_url = '/'

    def form_valid(self, form):
        request = self.request
        usuario = form.cleaned_data.get("Usuario")
        password = form.cleaned_data.get("password")
        remember_me = form.cleaned_data['remember_me']

        user = authenticate(request, username=usuario, password=password)
        if user is not None:
            login(request, user)
            if not remember_me:
                request.session.set_expiry(0)
            
            # Redirección estricta según el rol
            if user.admin or user.tecnico:
                return redirect('/Administracion/')
            return redirect('/Inicio/' + str(user.id) + '/')
            
        form.add_error(None, "Usuario o contraseña incorrectos.")
        return super().form_invalid(form)