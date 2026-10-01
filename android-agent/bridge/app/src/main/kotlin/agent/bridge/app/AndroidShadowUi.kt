package agent.bridge.app

import agent.bridge.core.ShadowUi
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.graphics.drawable.Icon

/** Shadow notifications (mvp-1-spec 3, stage 3). Posted by Bridge only, never by Python. */
class AndroidShadowUi(private val context: Context) : ShadowUi {
    private val nm: NotificationManager = requireNotNull(context.getSystemService(NotificationManager::class.java))

    init {
        nm.createNotificationChannel(NotificationChannel(CH_SHADOW, context.getString(R.string.channel_shadow), NotificationManager.IMPORTANCE_HIGH))
        nm.createNotificationChannel(NotificationChannel(CH_ALERTS, context.getString(R.string.channel_alerts), NotificationManager.IMPORTANCE_DEFAULT))
    }

    override fun show(token: String, senderName: String, proposedText: String) {
        val send = Notification.Action.Builder(
            Icon.createWithResource(context, android.R.drawable.ic_menu_send),
            context.getString(R.string.action_send),
            actionIntent(ShadowActionReceiver.ACTION_SEND, token),
        ).setAuthenticationRequired(true) // no send from a locked screen (mvp-1-spec 3.2)
            .build()
        val ignore = Notification.Action.Builder(
            Icon.createWithResource(context, android.R.drawable.ic_menu_close_clear_cancel),
            context.getString(R.string.action_ignore),
            actionIntent(ShadowActionReceiver.ACTION_IGNORE, token),
        ).build()

        val notification = Notification.Builder(context, CH_SHADOW)
            .setSmallIcon(android.R.drawable.sym_action_chat)
            .setContentTitle(context.getString(R.string.shadow_title, senderName))
            .setContentText(proposedText)
            .setStyle(Notification.BigTextStyle().bigText(proposedText))
            .addAction(send)
            .addAction(ignore)
            .setAutoCancel(false)
            .build()
        nm.notify(TAG, idFor(token), notification)
    }

    override fun cancel(token: String) = nm.cancel(TAG, idFor(token))

    override fun showExpired() = alert(context.getString(R.string.expired), null)

    override fun showSendFailed(senderName: String) = alert(context.getString(R.string.send_failed, senderName), null)

    override fun showUntrustedServer() =
        alert(context.getString(R.string.untrusted_server), context.getString(R.string.untrusted_server_detail))

    private fun alert(title: String, text: String?) {
        val n = Notification.Builder(context, CH_ALERTS)
            .setSmallIcon(android.R.drawable.stat_notify_error)
            .setContentTitle(title)
            .apply { if (text != null) setContentText(text) }
            .build()
        nm.notify(TAG_ALERT, title.hashCode(), n)
    }

    private fun actionIntent(action: String, token: String): PendingIntent {
        val intent = Intent(context, ShadowActionReceiver::class.java)
            .setAction(action)
            .putExtra(ShadowActionReceiver.EXTRA_TOKEN, token)
        return PendingIntent.getBroadcast(context, (action + token).hashCode(), intent,
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
    }

    private fun idFor(token: String) = token.hashCode()

    private companion object {
        const val CH_SHADOW = "shadow"
        const val CH_ALERTS = "alerts"
        const val TAG = "shadow"
        const val TAG_ALERT = "alert"
    }
}
