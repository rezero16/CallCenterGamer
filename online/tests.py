from unittest.mock import Mock, patch

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from online.ia_utils import AsistenteIA
from online.models import (
    Preguntas, Respuesta, Usuario, ConversacionIA, MensajeIA
)


class ConversationAccessTests(TestCase):
    def setUp(self):
        self.owner = self.create_user('owner', 'owner@example.com')
        self.other = self.create_user('other', 'other@example.com')
        self.question = Preguntas.objects.create(
            Titulo='Consulta',
            Pregunta='No inicia el juego',
            Fecha_creacion=timezone.now(),
            Usuario=self.owner,
        )

    def create_user(self, username, email, **fields):
        user = Usuario.objects.create(
            usuario=username,
            correo=email,
            **fields,
        )
        user.set_password('test-password-123')
        user.save()
        return user

    def test_user_cannot_view_another_users_conversation(self):
        self.client.force_login(self.other)

        response = self.client.get(
            reverse('respuestas', args=[self.other.id, self.question.idPregunta])
        )

        self.assertRedirects(response, f'/Inicio/{self.other.id}/')

    def test_admin_and_technician_can_view_conversations(self):
        for username, email, role in (
            ('admin', 'admin@example.com', {'admin': True}),
            ('tech', 'tech@example.com', {'tecnico': True}),
        ):
            with self.subTest(role=username):
                user = self.create_user(username, email, **role)
                self.client.force_login(user)
                response = self.client.get(
                    reverse('respuestas', args=[user.id, self.question.idPregunta])
                )
                self.assertEqual(response.status_code, 200)

    def test_reply_uses_url_question_not_tampered_hidden_question(self):
        another_question = Preguntas.objects.create(
            Titulo='Otra consulta',
            Pregunta='No hay audio',
            Fecha_creacion=timezone.now(),
            Usuario=self.other,
        )
        self.client.force_login(self.owner)

        response = self.client.post(
            reverse('respuestas', args=[self.owner.id, self.question.idPregunta]),
            {
                'Respuesta': 'Prueba reiniciar el juego.',
                'id_Pregunta': another_question.idPregunta,
                'Usuario': self.other.id,
                'FechaHora': timezone.now().isoformat(),
            },
        )

        self.assertRedirects(
            response,
            f'/Respuesta/{self.owner.id}/{self.question.idPregunta}/',
        )
        respuesta = Respuesta.objects.get()
        self.assertEqual(respuesta.id_Pregunta_id, self.question.idPregunta)
        self.assertEqual(respuesta.Usuario_id, self.owner.id)

    def test_logout_requires_post(self):
        self.client.force_login(self.owner)

        response = self.client.get(reverse('Logout'))

        self.assertEqual(response.status_code, 405)
        self.assertTrue(self.client.session.get('_auth_user_id'))

    def test_inactive_users_cannot_log_in(self):
        self.owner.activo = False
        self.owner.save()

        response = self.client.post(
            reverse('Login'),
            {'Usuario': self.owner.usuario, 'password': 'test-password-123'},
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse('_auth_user_id' in self.client.session)
        self.assertTrue(response.context['form'].non_field_errors())


class ChatIASeparationTests(TestCase):
    def setUp(self):
        self.owner = self.create_user('owner', 'owner@example.com')
        self.other = self.create_user('other', 'other@example.com')

    def create_user(self, username, email, **fields):
        user = Usuario.objects.create(
            usuario=username,
            correo=email,
            **fields,
        )
        user.set_password('test-password-123')
        user.save()
        return user

    @patch(
        'online.views.AsistenteIA.generar_respuesta_automatica',
        return_value='Respuesta simulada de IA',
    )
    def test_chat_saves_ai_messages_outside_consultation_tables(self, mocked_ia):
        self.client.force_login(self.owner)

        response = self.client.post(
            reverse('chat_ia', args=[self.owner.id]),
            {'mensaje': 'No me inicia el juego'},
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['exito'])
        self.assertEqual(Preguntas.objects.count(), 0)
        self.assertEqual(Respuesta.objects.count(), 0)
        self.assertEqual(ConversacionIA.objects.count(), 1)
        self.assertEqual(MensajeIA.objects.count(), 2)

        conversacion = ConversacionIA.objects.get()
        self.assertEqual(conversacion.Usuario_id, self.owner.id)
        self.assertEqual(
            list(conversacion.mensajes.values_list('Rol', flat=True)),
            [MensajeIA.ROL_USUARIO, MensajeIA.ROL_IA],
        )
        self.assertEqual(mocked_ia.call_args.kwargs['historial_mensajes'], [])

    @patch(
        'online.views.AsistenteIA.generar_respuesta_automatica',
        return_value='Respuesta simulada de IA',
    )
    def test_ai_context_uses_only_the_selected_conversation(self, mocked_ia):
        self.client.force_login(self.owner)
        conversacion = ConversacionIA.objects.create(
            Usuario=self.owner,
            Titulo='Conversación anterior',
        )
        MensajeIA.objects.create(
            Conversacion=conversacion,
            Rol=MensajeIA.ROL_USUARIO,
            Contenido='Mensaje anterior',
        )
        MensajeIA.objects.create(
            Conversacion=conversacion,
            Rol=MensajeIA.ROL_IA,
            Contenido='Respuesta anterior',
        )
        Preguntas.objects.create(
            Titulo='Consulta normal',
            Pregunta='Esto no debe entrar al contexto de la IA',
            Fecha_creacion=timezone.now(),
            Usuario=self.owner,
        )

        self.client.post(
            reverse('chat_ia', args=[self.owner.id]),
            {
                'mensaje': 'Nuevo mensaje',
                'conversacion_id': conversacion.idConversacion,
            },
        )

        historial = mocked_ia.call_args.kwargs['historial_mensajes']
        self.assertEqual(historial, [
            {'rol': MensajeIA.ROL_USUARIO, 'contenido': 'Mensaje anterior'},
            {'rol': MensajeIA.ROL_IA, 'contenido': 'Respuesta anterior'},
        ])
        self.assertEqual(conversacion.mensajes.count(), 4)

    @patch(
        'online.views.AsistenteIA.generar_respuesta_automatica',
        return_value='Respuesta simulada de IA',
    )
    def test_user_cannot_send_messages_to_another_users_chat(self, mocked_ia):
        self.client.force_login(self.other)

        response = self.client.post(
            reverse('chat_ia', args=[self.owner.id]),
            {'mensaje': 'Hola'},
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(response.json()['exito'])
        self.assertEqual(ConversacionIA.objects.count(), 0)
        mocked_ia.assert_not_called()

    def test_inicio_keeps_ai_conversations_and_consultations_separate(self):
        pregunta = Preguntas.objects.create(
            Titulo='Consulta normal',
            Pregunta='No tengo audio',
            Fecha_creacion=timezone.now(),
            Usuario=self.owner,
        )
        conversacion = ConversacionIA.objects.create(
            Usuario=self.owner,
            Titulo='Chat de IA',
        )
        MensajeIA.objects.create(
            Conversacion=conversacion,
            Rol=MensajeIA.ROL_USUARIO,
            Contenido='Mensaje de IA',
        )
        self.client.force_login(self.owner)

        response = self.client.get(reverse('inicio', args=[self.owner.id]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context['Preguntas']), [pregunta])
        self.assertEqual(list(response.context['conversaciones_ia']), [conversacion])
        self.assertEqual(response.context['conversacion_actual'], conversacion)
        self.assertEqual(len(response.context['mensajes_ia']), 1)

    def test_inicio_renders_chat_script_and_posts_to_chat_endpoint(self):
        self.client.force_login(self.owner)

        response = self.client.get(reverse('inicio', args=[self.owner.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="form-mensaje"')
        self.assertContains(response, 'fetch(chatUrl')
        self.assertContains(response, 'text/event-stream')
        self.assertContains(response, 'getReader()')
        self.assertContains(
            response,
            f'action="{reverse("chat_ia", args=[self.owner.id])}"',
        )

    @patch(
        'online.views.AsistenteIA.generar_respuesta_stream',
        return_value=iter(['Hola ', 'mundo']),
    )
    def test_chat_streams_sse_and_saves_complete_response(self, mocked_stream):
        self.client.force_login(self.owner)

        response = self.client.post(
            reverse('chat_ia', args=[self.owner.id]),
            {'mensaje': 'Hola'},
            HTTP_ACCEPT='text/event-stream',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'text/event-stream')
        self.assertEqual(response['Cache-Control'], 'no-cache')
        self.assertEqual(response['X-Accel-Buffering'], 'no')

        contenido = b''.join(response.streaming_content).decode('utf-8')
        self.assertIn('event: meta', contenido)
        self.assertIn('event: delta', contenido)
        self.assertIn('event: done', contenido)
        self.assertNotIn('event: error', contenido)

        conversacion = ConversacionIA.objects.get()
        self.assertEqual(conversacion.mensajes.count(), 2)
        respuesta_ia = conversacion.mensajes.get(Rol=MensajeIA.ROL_IA)
        self.assertEqual(respuesta_ia.Contenido, 'Hola mundo')
        self.assertEqual(
            mocked_stream.call_args.kwargs['historial_mensajes'],
            [],
        )

    def test_non_question_post_to_inicio_is_not_validated_as_question(self):
        self.client.force_login(self.owner)

        response = self.client.post(
            reverse('inicio', args=[self.owner.id]),
            {'mensaje': 'Mensaje enviado al chat de IA'},
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['form'].is_bound)
        self.assertEqual(Preguntas.objects.count(), 0)

    def test_question_form_still_creates_question_from_inicio(self):
        self.client.force_login(self.owner)

        response = self.client.post(
            reverse('inicio', args=[self.owner.id]),
            {
                'Titulo': 'No inicia el juego',
                'Pregunta': 'El juego se cierra al abrir.',
                'Usuario': self.owner.id,
                'Fecha_creacion': timezone.now().isoformat(),
            },
        )

        self.assertRedirects(response, f'/Inicio/{self.owner.id}/')
        pregunta = Preguntas.objects.get()
        self.assertEqual(pregunta.Usuario_id, self.owner.id)
        self.assertEqual(pregunta.Titulo, 'No inicia el juego')


class AsistenteLocalTests(TestCase):
    @override_settings(GEMINI_API_KEY='')
    def test_local_mode_returns_a_useful_answer_without_api_key(self):
        asistente = AsistenteIA()

        respuesta = asistente.generar_respuesta_automatica(
            'El juego se cierra con un error al iniciar'
        )

        self.assertIn('juego', respuesta.lower())
        self.assertNotIn('GEMINI_API_KEY', respuesta)
        self.assertNotIn('Error:', respuesta)

    @override_settings(GEMINI_API_KEY='')
    def test_local_classification_works_without_api_key(self):
        asistente = AsistenteIA()

        self.assertEqual(
            asistente.clasificar_pregunta('Mi tarjeta grafica se calienta'),
            'hardware',
        )
        self.assertEqual(
            asistente.clasificar_pregunta('No puedo recuperar mi contrasena'),
            'cuenta',
        )

    @override_settings(GEMINI_API_KEY='clave-de-prueba')
    def test_classification_stays_local_even_with_api_key(self):
        asistente = AsistenteIA()

        with patch.object(asistente, '_client') as mocked_client:
            categoria = asistente.clasificar_pregunta(
                'Mi tarjeta grafica se calienta'
            )

        self.assertEqual(categoria, 'hardware')
        mocked_client.assert_not_called()


class AsistenteGeminiTests(TestCase):
    @override_settings(
        GEMINI_API_KEY='clave-de-prueba',
        GEMINI_MODEL='gemini-de-prueba',
    )
    def test_gemini_receives_history_and_returns_response_text(self):
        respuesta_remota = Mock(text='Respuesta generada por Gemini')
        chat = Mock()
        chat.send_message.return_value = respuesta_remota
        cliente = Mock()
        cliente.chats.create.return_value = chat
        asistente = AsistenteIA()

        with patch.object(asistente, '_client', return_value=cliente):
            respuesta = asistente.generar_respuesta_automatica(
                'Nuevo mensaje',
                historial_mensajes=[
                    {'rol': MensajeIA.ROL_USUARIO, 'contenido': 'Mensaje anterior'},
                    {'rol': MensajeIA.ROL_IA, 'contenido': 'Respuesta anterior'},
                ],
            )

        self.assertEqual(respuesta, 'Respuesta generada por Gemini')
        chat.send_message.assert_called_once_with('Nuevo mensaje')
        argumentos = cliente.chats.create.call_args.kwargs
        self.assertEqual(argumentos['model'], 'gemini-de-prueba')
        self.assertEqual(
            [contenido.role for contenido in argumentos['history']],
            ['user', 'model'],
        )
        self.assertEqual(
            argumentos['history'][0].parts[0].text,
            'Mensaje anterior',
        )
        self.assertEqual(
            argumentos['config'].max_output_tokens,
            AsistenteIA.MAX_TOKENS_RESPUESTA,
        )
        self.assertIn(
            'Call Center Gamer',
            argumentos['config'].system_instruction,
        )

    @override_settings(GEMINI_API_KEY='clave-de-prueba')
    def test_gemini_failure_falls_back_to_local_response(self):
        cliente = Mock()
        cliente.chats.create.side_effect = RuntimeError('servicio no disponible')
        asistente = AsistenteIA()

        with patch.object(asistente, '_client', return_value=cliente):
            with self.assertLogs('online.ia_utils', level='ERROR'):
                respuesta = asistente.generar_respuesta_automatica(
                    'Mi tarjeta grafica se calienta'
                )

        self.assertIn('hardware', respuesta.lower())
        self.assertNotIn('servicio no disponible', respuesta)

    @override_settings(
        GEMINI_API_KEY='clave-de-prueba',
        GEMINI_TIMEOUT_MS=2500,
    )
    def test_gemini_client_uses_fast_timeout_without_retries(self):
        asistente = AsistenteIA()

        with patch('online.ia_utils.genai.Client') as mocked_client_class:
            asistente._client()

        opciones = mocked_client_class.call_args.kwargs['http_options']
        self.assertEqual(opciones.timeout, 2500)
        self.assertEqual(opciones.retry_options.attempts, 1)
        self.assertEqual(opciones.headers['X-Server-Timeout'], '10')

    @override_settings(
        GEMINI_API_KEY='clave-de-prueba',
        GEMINI_MODEL='gemini-de-prueba',
    )
    def test_gemini_stream_yields_chunks_with_fast_config(self):
        chat = Mock()
        chat.send_message_stream.return_value = iter([
            Mock(text='Hola '),
            Mock(text='mundo'),
        ])
        cliente = Mock()
        cliente.chats.create.return_value = chat
        asistente = AsistenteIA()

        with patch.object(asistente, '_client', return_value=cliente):
            fragmentos = list(asistente.generar_respuesta_stream('Hola'))

        self.assertEqual(fragmentos, ['Hola ', 'mundo'])
        chat.send_message_stream.assert_called_once_with('Hola')
        config = cliente.chats.create.call_args.kwargs['config']
        self.assertEqual(
            config.thinking_config.thinking_level.value,
            AsistenteIA.NIVEL_RAZONAMIENTO,
        )

    @override_settings(GEMINI_API_KEY='clave-de-prueba')
    def test_gemini_stream_failure_falls_back_to_local_response(self):
        chat = Mock()
        chat.send_message_stream.side_effect = RuntimeError('sin conexión')
        cliente = Mock()
        cliente.chats.create.return_value = chat
        asistente = AsistenteIA()

        with patch.object(asistente, '_client', return_value=cliente):
            with self.assertLogs('online.ia_utils', level='ERROR'):
                fragmentos = list(asistente.generar_respuesta_stream(
                    'Mi tarjeta grafica se calienta'
                ))

        self.assertEqual(len(fragmentos), 1)
        self.assertIn('hardware', fragmentos[0].lower())
        self.assertNotIn('sin conexión', fragmentos[0])

    def test_history_starts_with_user_and_merges_consecutive_roles(self):
        asistente = AsistenteIA()

        historial = asistente._historial_para_gemini([
            {'rol': MensajeIA.ROL_IA, 'contenido': 'Mensaje inicial de la IA'},
            {'rol': MensajeIA.ROL_USUARIO, 'contenido': 'Primera pregunta'},
            {'rol': MensajeIA.ROL_USUARIO, 'contenido': 'Segunda pregunta'},
            {'rol': MensajeIA.ROL_IA, 'contenido': 'Primera respuesta'},
            {'rol': MensajeIA.ROL_IA, 'contenido': 'Segunda respuesta'},
            {'rol': MensajeIA.ROL_USUARIO, 'contenido': '   '},
        ])

        self.assertEqual(
            [contenido.role for contenido in historial],
            ['user', 'model'],
        )
        self.assertEqual(
            historial[0].parts[0].text,
            'Primera pregunta\n\nSegunda pregunta',
        )
        self.assertEqual(
            historial[1].parts[0].text,
            'Primera respuesta\n\nSegunda respuesta',
        )
