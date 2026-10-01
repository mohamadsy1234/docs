package agent.bridge.app

import agent.bridge.core.BridgeCore
import android.content.Context

/** Process-wide handle so the action receiver can reach the running core. */
object AgentBridge {
    @Volatile
    var core: BridgeCore<ReplyHandle>? = null
}

class Prefs(context: Context) {
    private val prefs = context.getSharedPreferences("bridge", Context.MODE_PRIVATE)

    /** Cloud-LLM consent (mvp-1-spec 5). Nothing is forwarded while false. */
    var consent: Boolean
        get() = prefs.getBoolean("consent", false)
        set(value) = prefs.edit().putBoolean("consent", value).apply()

    val port: Int get() = prefs.getInt("port", 8000)
}
