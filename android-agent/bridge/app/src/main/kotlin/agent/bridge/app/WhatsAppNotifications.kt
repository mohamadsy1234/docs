package agent.bridge.app

import agent.bridge.core.PostedNotification
import android.app.Notification
import android.service.notification.StatusBarNotification
import androidx.core.app.NotificationCompat

/** Turns a WhatsApp StatusBarNotification into what BridgeCore needs (mvp-1-spec 3, stage 1). */
object WhatsAppNotifications {
    const val PACKAGE = "com.whatsapp"

    fun parse(sbn: StatusBarNotification): PostedNotification<ReplyHandle>? {
        if (sbn.packageName != PACKAGE) return null
        val n = sbn.notification
        if (n.flags and Notification.FLAG_GROUP_SUMMARY != 0) return null

        val style = NotificationCompat.MessagingStyle.extractMessagingStyleFromNotification(n) ?: return null
        val last = style.messages.lastOrNull() ?: return null

        // Our own reply comes back as the newest message once WhatsApp updates the
        // notification; answering it would make the agent talk to itself.
        val from = last.person
        // Compare as String: a SpannableString never equals a String with the same text.
        if (from == null || (from.key != null && from.key == style.user.key) ||
            from.name?.toString() == style.user.name?.toString()) return null

        val action = n.actions?.firstOrNull { a -> a.remoteInputs?.any { it.allowFreeFormInput } == true }
        val inputs = action?.remoteInputs
        val input = inputs?.firstOrNull { it.allowFreeFormInput }
        val handle = if (action != null && inputs != null && input != null) {
            ReplyHandle(action.actionIntent, inputs, input.resultKey)
        } else null

        return PostedNotification(
            sbnKey = sbn.key,
            messageTime = last.timestamp,
            senderName = from.name?.toString()
                ?: style.conversationTitle?.toString()
                ?: n.extras.getCharSequence(Notification.EXTRA_TITLE)?.toString()
                ?: "?",
            text = if (last.dataMimeType != null) null else last.text?.toString(), // media -> null
            isGroup = style.isGroupConversation,
            replyHandle = handle,
        )
    }
}
