package agent.bridge.app

import agent.bridge.core.ReplySender
import android.app.PendingIntent
import android.app.RemoteInput
import android.content.Context
import android.content.Intent
import android.os.Bundle

/** WhatsApp's own reply action; lives only inside Bridge (mvp-1-spec 3, stage 1). */
class ReplyHandle(
    val action: PendingIntent,
    val remoteInputs: Array<RemoteInput>,
    val resultKey: String,
)

class NotificationReplySender(private val context: Context) : ReplySender<ReplyHandle> {
    override fun send(handle: ReplyHandle, text: String): Boolean {
        val intent = Intent()
        val results = Bundle().apply { putCharSequence(handle.resultKey, text) }
        RemoteInput.addResultsToIntent(handle.remoteInputs, intent, results)
        return try {
            handle.action.send(context, 0, intent)
            true
        } catch (e: PendingIntent.CanceledException) {
            false // WhatsApp withdrew the notification: the reply handle is dead
        }
    }
}
