# Configurar notificaciones por correo — Orquídea Smart

El sistema envía recordatorios a **orquidea.smart@gmail.com** para cada día y hora registrados en el calendario, interpretando horarios en **America/Santiago**. Se registra cada envío en la tabla `email_notification_events` dentro de la **misma base de Neon**, sin borrar las demás tablas. La tabla se crea automáticamente al iniciar la app.

## Qué configurar en Render

Mantén el `DATABASE_URL` de Neon que ya funciona. En **Environment** agrega:

| Variable | Valor |
|---|---|
| `EMAIL_PROVIDER` | `resend` |
| `RESEND_API_KEY` | API key privada de Resend |
| `NOTIFICATION_EMAIL` | `orquidea.smart@gmail.com` |
| `EMAIL_FROM` | `Orquídea Smart <onboarding@resend.dev>` únicamente para pruebas a la cuenta titular de Resend; para otros destinatarios verifica un dominio |
| `NOTIFICATION_TIMEZONE` | `America/Santiago` |
| `NOTIFICATION_GRACE_MINUTES` | `30` |
| `NOTIFICATION_JOB_KEY` | Una cadena secreta aleatoria de 24 caracteres o más |
| `ADMIN_PASSWORD` | Contraseña privada del panel; debe estar definida |

**No publiques** ni la API key ni la conexión de Neon en GitHub. En Render gratuito los puertos SMTP suelen estar bloqueados; el proyecto admite la API HTTPS de Resend.

Puedes registrarte en https://resend.com con la cuenta destinataria. Antes de verificar un dominio, el remitente de pruebas de Resend solo envía al correo propietario de la cuenta. Si necesitas enviar a una dirección distinta, verifica un dominio propio y cambia `EMAIL_FROM`.

## Programador para que funcione con la página cerrada

Como Render gratuito no mantiene procesos de fondo permanentes, crea en https://cron-job.org/en/ un trabajo HTTP:

- Método: **POST**
- URL: `https://TU-SERVICIO.onrender.com/api/notifications/dispatch`
- Frecuencia: **cada minuto**
- Header: `X-Notification-Key` con exactamente el valor secreto de `NOTIFICATION_JOB_KEY`
- Body: vacío.

El cron es necesario: sin él, los correos no salen automáticamente. El intervalo real depende de Render y del programador; la ventana de recuperación por defecto es 30 minutos. No pongas la clave en la URL.

## Comprobación

En el panel ingresa un riego para un día y hora cercanos; abre **Notificaciones por correo** y pulsa **Enviar correo de prueba**. Si da error, revisa Render → Logs y la configuración del proveedor. Un envío aceptado no garantiza entrega inmediata; revisa también Spam.

Para ejecución local, `python notification_worker.py` ejecuta el ciclo (o `python notification_worker.py --once` para una sola comprobación), siempre que el servicio de correo esté configurado. Para ejecutar pruebas: `python -m pytest -q`.

El PR no activa ni verifica de forma automática las credenciales externas de Resend o el cron, y no debe fusionarse con una implementación existente antes de revisar sus resultados.
