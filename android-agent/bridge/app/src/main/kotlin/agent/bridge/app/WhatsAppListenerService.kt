package agent.bridge.app

import agent.bridge.core.BridgeClient
import agent.bridge.core.BridgeCore
import android.content.ComponentName
import android.os.Handler
import android.os.Looper
import android.service.notification.NotificationListenerService
import android.service.notification.StatusBarNotification

/**
 * Entry point for WhatsApp notifications, and host of the agent session for
 * as long as the system keeps the listener bound.
 */
class WhatsAppListenerService : NotificationListenerService() {
    private var client: BridgeClient? = null
    private var core: BridgeCore<ReplyHandle>? = null
    private val handler = Handler(Looper.getMainLooper())

    private val sweep = object : Runnable {
        override fun run() {
            core?.sweep()
            handler.postDelayed(this, SWEEP_INTERVAL_MS)
        }
    }

    override fun onListenerConnected() {
        val ctx = applicationContext
        val prefs = Prefs(ctx)
        var bridgeClient: BridgeClient? = null
        val bridgeCore = BridgeCore(
            transport = { message -> bridgeClient?.send(message) ?: false },
            ui = AndroidShadowUi(ctx),
            sender = NotificationReplySender(ctx),
            audit = AuditLogFile(ctx),
            hasConsent = { prefs.consent },
        )
        bridgeClient = BridgeClient("ws://127.0.0.1:${prefs.port}", SecretStore(ctx).getOrCreate(), bridgeCore)
        core = bridgeCore
        client = bridgeClient
        AgentBridge.core = bridgeCore
        bridgeClient.start()
        handler.postDelayed(sweep, SWEEP_INTERVAL_MS)
    }

    override fun onListenerDisconnected() {
        handler.removeCallbacks(sweep)
        client?.stop()
        client = null
        core = null
        AgentBridge.core = null
        requestRebind(ComponentName(this, WhatsAppListenerService::class.java))
    }

    override fun onNotificationPosted(sbn: StatusBarNotification) {
        val c = core ?: return
        WhatsAppNotifications.parse(sbn)?.let(c::onNotificationPosted)
    }

    override fun onNotificationRemoved(sbn: StatusBarNotification) {
        if (sbn.packageName == WhatsAppNotifications.PACKAGE) core?.onNotificationRemoved(sbn.key)
    }

    private companion object {
        const val SWEEP_INTERVAL_MS = 60 * 60 * 1000L
    }
}
