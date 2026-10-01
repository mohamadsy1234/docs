package agent.bridge.app

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent

/** [Send now] / [Ignore] on a shadow notification (mvp-1-spec 3, stage 4). */
class ShadowActionReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val token = intent.getStringExtra(EXTRA_TOKEN) ?: return
        val core = AgentBridge.core
        if (core == null) {
            // Bridge restarted since the proposal was shown: its tokens are gone.
            AndroidShadowUi(context).apply { cancel(token); showExpired() }
            return
        }
        when (intent.action) {
            ACTION_SEND -> core.onUserSend(token)
            ACTION_IGNORE -> core.onUserIgnore(token)
        }
    }

    companion object {
        const val ACTION_SEND = "agent.bridge.app.SEND"
        const val ACTION_IGNORE = "agent.bridge.app.IGNORE"
        const val EXTRA_TOKEN = "token"
    }
}
