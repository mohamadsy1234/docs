package agent.bridge.app

import agent.bridge.core.toHex
import android.Manifest
import android.app.Activity
import android.app.NotificationManager
import android.content.ClipData
import android.content.ClipDescription
import android.content.ClipboardManager
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Color
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.PersistableBundle
import android.provider.Settings
import android.text.format.DateFormat
import android.view.Gravity
import android.view.View
import android.view.ViewGroup.LayoutParams.MATCH_PARENT
import android.view.ViewGroup.LayoutParams.WRAP_CONTENT
import android.widget.Button
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.Switch
import android.widget.TextView
import android.widget.Toast

/**
 * The app's one screen, in the NEXUS look: live status, pairing, consent and
 * permissions (mvp-1-spec 2.1, 5). Sending is not possible from here: only
 * from the shadow notification, behind the device unlock.
 */
class MainActivity : Activity() {
    private lateinit var prefs: Prefs
    private lateinit var secretHex: String
    private var secretShown = false
    private val handler = Handler(Looper.getMainLooper())
    private val refresher = object : Runnable {
        override fun run() { render(); handler.postDelayed(this, 1000) }
    }

    // Views updated on every refresh.
    private lateinit var statusLine: TextView
    private lateinit var warning: TextView
    private lateinit var agentRow: StatusRow
    private lateinit var listenerRow: StatusRow
    private lateinit var postRow: StatusRow
    private lateinit var consentRow: StatusRow
    private lateinit var mForwarded: TextView
    private lateinit var mSent: TextView
    private lateinit var mIgnored: TextView
    private lateinit var mExpired: TextView
    private lateinit var secretView: TextView
    private lateinit var showBtn: Button
    private lateinit var consentSwitch: Switch
    private lateinit var events: LinearLayout

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        prefs = Prefs(this)
        secretHex = SecretStore(this).getOrCreate().toHex()
        window.statusBarColor = BG
        window.navigationBarColor = BG

        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            layoutDirection = View.LAYOUT_DIRECTION_RTL
            setPadding(dp(16), dp(20), dp(16), dp(24))
        }

        root.addView(header())
        warning = text(14, Color.WHITE, bold = true).apply {
            background = panel(fill = 0x33FF3D71, stroke = CRIMSON)
            setPadding(dp(14), dp(12), dp(14), dp(12))
            visibility = View.GONE
            text = getString(R.string.untrusted_server_detail)
        }
        root.addView(warning, spaced())

        root.addView(section(getString(R.string.section_status)) { box ->
            agentRow = StatusRow(getString(R.string.row_agent)).also { box.addView(it.view) }
            listenerRow = StatusRow(getString(R.string.row_listener), getString(R.string.fix)) {
                startActivity(Intent(Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS))
            }.also { box.addView(it.view) }
            postRow = StatusRow(getString(R.string.row_post), getString(R.string.fix)) { requestPost() }
                .also { box.addView(it.view) }
            consentRow = StatusRow(getString(R.string.row_consent)).also { box.addView(it.view) }
        })

        val grid1 = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL }
        val grid2 = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL }
        mForwarded = metric(grid1, getString(R.string.m_forwarded), CYAN)
        mSent = metric(grid1, getString(R.string.m_sent), EMERALD)
        mIgnored = metric(grid2, getString(R.string.m_ignored), AMBER)
        mExpired = metric(grid2, getString(R.string.m_expired), PURPLE)
        root.addView(grid1, spaced())
        root.addView(grid2, spaced())

        root.addView(section(getString(R.string.section_pairing)) { box ->
            box.addView(text(13, MUTED).apply { text = getString(R.string.secret_label) })
            secretView = text(14, CYAN).apply {
                typeface = Typeface.MONOSPACE
                textDirection = View.TEXT_DIRECTION_LTR
                background = panel(fill = 0x99000000.toInt(), stroke = 0x1AFFFFFF)
                setPadding(dp(12), dp(10), dp(12), dp(10))
            }
            box.addView(secretView, spaced(8))
            val row = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL }
            row.addView(button(getString(R.string.copy), CYAN, filled = true) { copySecret() }, weight())
            showBtn = button(getString(R.string.show), MUTED) { secretShown = !secretShown; render() }
            row.addView(showBtn, weight(startMargin = 8))
            box.addView(row, spaced(10))
        })

        root.addView(section(getString(R.string.section_consent)) { box ->
            consentSwitch = Switch(this).apply {
                text = getString(R.string.consent)
                setTextColor(TEXT)
                textSize = 14f
                isChecked = prefs.consent
                setOnCheckedChangeListener { _, checked -> prefs.consent = checked; render() }
            }
            box.addView(consentSwitch)
        })

        root.addView(section(getString(R.string.section_events)) { box ->
            events = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
            box.addView(events)
        })

        root.addView(text(12, MUTED).apply {
            text = getString(R.string.footer_send_rule)
            gravity = Gravity.CENTER
            setPadding(dp(8), dp(16), dp(8), 0)
        })

        setContentView(ScrollView(this).apply {
            setBackgroundColor(BG)
            isFillViewport = true
            addView(root)
        })
    }

    override fun onResume() {
        super.onResume()
        handler.post(refresher)
    }

    override fun onPause() {
        handler.removeCallbacks(refresher)
        super.onPause()
    }

    private fun render() {
        val s = AgentBridge.stats
        val listenerOn = listenerEnabled()
        val postOn = postAllowed()
        val ready = s.agentConnected && listenerOn && postOn && prefs.consent

        statusLine.text = getString(R.string.status_line, if (ready) "OPERATIONAL" else "SETUP NEEDED")
        statusLine.setTextColor(if (ready) EMERALD else AMBER)
        warning.visibility = if (s.untrustedServer) View.VISIBLE else View.GONE

        agentRow.set(s.agentConnected, getString(if (s.agentConnected) R.string.agent_on else R.string.agent_off))
        listenerRow.set(listenerOn, getString(if (listenerOn) R.string.on else R.string.off))
        postRow.set(postOn, getString(if (postOn) R.string.on else R.string.off))
        consentRow.set(prefs.consent, getString(if (prefs.consent) R.string.on else R.string.off))

        mForwarded.text = s.count("forwarded").toString()
        mSent.text = s.count("sent").toString()
        mIgnored.text = s.count("ignored").toString()
        mExpired.text = (s.count("expired") + s.count("send_failed") + s.count("send_expired")).toString()

        secretView.text = if (secretShown) secretHex else "••••••••••••••••••••••••" + secretHex.takeLast(6)
        showBtn.text = getString(if (secretShown) R.string.hide else R.string.show)
        if (consentSwitch.isChecked != prefs.consent) consentSwitch.isChecked = prefs.consent

        events.removeAllViews()
        val list = s.recentEvents().take(10)
        if (list.isEmpty()) {
            events.addView(text(13, MUTED).apply { text = getString(R.string.no_events) })
        }
        list.forEach { e ->
            val row = LinearLayout(this).apply {
                orientation = LinearLayout.HORIZONTAL
                setPadding(0, dp(6), 0, dp(6))
            }
            row.addView(text(12, MUTED).apply {
                typeface = Typeface.MONOSPACE
                text = DateFormat.format("HH:mm:ss", e.time)
            }, LinearLayout.LayoutParams(WRAP_CONTENT, WRAP_CONTENT))
            row.addView(text(13, colorFor(e.name)).apply {
                text = label(e.name) + (e.sender?.let { " · $it" } ?: "")
                setPadding(dp(12), 0, 0, 0)
            }, LinearLayout.LayoutParams(0, WRAP_CONTENT, 1f))
            events.addView(row)
        }
    }

    private fun listenerEnabled(): Boolean =
        Settings.Secure.getString(contentResolver, "enabled_notification_listeners")?.contains(packageName) == true

    private fun postAllowed(): Boolean {
        val granted = Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU ||
            checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) == PackageManager.PERMISSION_GRANTED
        return granted && getSystemService(NotificationManager::class.java)?.areNotificationsEnabled() == true
    }

    private fun requestPost() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), 1)
        } else {
            startActivity(Intent(Settings.ACTION_APP_NOTIFICATION_SETTINGS).putExtra(Settings.EXTRA_APP_PACKAGE, packageName))
        }
    }

    private fun copySecret() {
        val clip = ClipData.newPlainText("API_SECRET", "API_SECRET=$secretHex")
        // Keep the secret out of clipboard previews (Android 13+).
        clip.description.extras = PersistableBundle().apply {
            putBoolean(ClipDescription.EXTRA_IS_SENSITIVE, true)
        }
        getSystemService(ClipboardManager::class.java)?.setPrimaryClip(clip)
        Toast.makeText(this, R.string.copied, Toast.LENGTH_SHORT).show()
    }

    private fun label(event: String): String = when (event) {
        "forwarded" -> getString(R.string.e_forwarded)
        "sent" -> getString(R.string.e_sent)
        "ignored" -> getString(R.string.e_ignored)
        "expired" -> getString(R.string.e_expired)
        "send_failed" -> getString(R.string.e_send_failed)
        "send_expired" -> getString(R.string.e_send_expired)
        "session_opened" -> getString(R.string.e_session_opened)
        "session_closed" -> getString(R.string.e_session_closed)
        "handshake_failed" -> getString(R.string.e_handshake_failed)
        "dropped_offline" -> getString(R.string.e_dropped_offline)
        "proposal_ignored_unknown_token" -> getString(R.string.e_unknown_token)
        else -> event
    }

    private fun colorFor(event: String): Int = when (event) {
        "sent", "session_opened" -> EMERALD
        "ignored", "expired", "dropped_offline", "session_closed" -> AMBER
        "send_failed", "send_expired", "handshake_failed", "proposal_ignored_unknown_token" -> CRIMSON
        else -> CYAN
    }

    // --- building blocks ---

    private inner class StatusRow(title: String, fixLabel: String? = null, onFix: (() -> Unit)? = null) {
        val view = LinearLayout(this@MainActivity).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            setPadding(0, dp(8), 0, dp(8))
        }
        private val dot = View(this@MainActivity)
        private val value = text(13, MUTED)
        private val fix = fixLabel?.let { button(it, CYAN) { onFix?.invoke() } }

        init {
            view.addView(dot, LinearLayout.LayoutParams(dp(10), dp(10)).apply { marginEnd = dp(10) })
            view.addView(text(14, TEXT).apply { text = title }, LinearLayout.LayoutParams(0, WRAP_CONTENT, 1f))
            view.addView(value)
            fix?.let { view.addView(it, LinearLayout.LayoutParams(WRAP_CONTENT, dp(44)).apply { marginStart = dp(10) }) }
        }

        fun set(ok: Boolean, label: String) {
            dot.background = GradientDrawable().apply { shape = GradientDrawable.OVAL; setColor(if (ok) EMERALD else CRIMSON) }
            value.text = label
            value.setTextColor(if (ok) EMERALD else AMBER)
            fix?.visibility = if (ok) View.GONE else View.VISIBLE
        }
    }

    private fun header(): View {
        val row = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            setPadding(0, 0, 0, dp(16))
        }
        val logo = TextView(this).apply {
            text = "N"
            setTextColor(CYAN)
            textSize = 20f
            typeface = Typeface.create(Typeface.MONOSPACE, Typeface.BOLD)
            gravity = Gravity.CENTER
            background = panel(fill = 0x1A00F0FF, stroke = 0x6600F0FF, radius = 12)
        }
        row.addView(logo, LinearLayout.LayoutParams(dp(44), dp(44)).apply { marginEnd = dp(12) })
        val titles = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        titles.addView(text(20, Color.WHITE).apply {
            typeface = Typeface.create(Typeface.MONOSPACE, Typeface.BOLD)
            text = "NEXUS.AI"
            textDirection = View.TEXT_DIRECTION_LTR
        })
        statusLine = text(11, EMERALD).apply { typeface = Typeface.MONOSPACE; textDirection = View.TEXT_DIRECTION_LTR }
        titles.addView(statusLine)
        row.addView(titles, LinearLayout.LayoutParams(0, WRAP_CONTENT, 1f))
        row.addView(text(10, AMBER).apply {
            typeface = Typeface.create(Typeface.MONOSPACE, Typeface.BOLD)
            text = "SHADOW · MVP-1"
            background = panel(fill = 0x1AFFB800, stroke = 0x66FFB800, radius = 8)
            setPadding(dp(8), dp(4), dp(8), dp(4))
        })
        return row
    }

    private fun section(title: String, fill: (LinearLayout) -> Unit): View {
        val box = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            background = panel()
            setPadding(dp(16), dp(14), dp(16), dp(14))
        }
        box.addView(text(16, Color.WHITE, bold = true).apply { text = title; setPadding(0, 0, 0, dp(8)) })
        fill(box)
        return box.also { it.layoutParams = spaced() }
    }

    private fun metric(parent: LinearLayout, title: String, color: Int): TextView {
        val box = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            background = panel()
            setPadding(dp(14), dp(12), dp(14), dp(12))
        }
        box.addView(text(12, MUTED).apply { text = title })
        val value = text(28, color, bold = true).apply { typeface = Typeface.create(Typeface.MONOSPACE, Typeface.BOLD); text = "0" }
        box.addView(value)
        parent.addView(box, weight(startMargin = if (parent.childCount > 0) 10 else 0))
        return value
    }

    private fun button(label: String, color: Int, filled: Boolean = false, onClick: () -> Unit) =
        Button(this).apply {
            text = label
            isAllCaps = false
            textSize = 14f
            setTextColor(if (filled) BG else color)
            background = panel(fill = if (filled) color else 0x1A000000 or (color and 0x00FFFFFF), stroke = color, radius = 12)
            minHeight = dp(44)
            minimumHeight = dp(44)
            setPadding(dp(16), 0, dp(16), 0)
            setOnClickListener { onClick() }
        }

    private fun text(sizeSp: Int, color: Int, bold: Boolean = false) = TextView(this).apply {
        textSize = sizeSp.toFloat()
        setTextColor(color)
        if (bold) setTypeface(typeface, Typeface.BOLD)
        setLineSpacing(0f, 1.15f)
    }

    private fun panel(fill: Int = PANEL, stroke: Int = BORDER, radius: Int = 16) = GradientDrawable().apply {
        setColor(fill)
        setStroke(dp(1), stroke)
        cornerRadius = dp(radius).toFloat()
    }

    private fun spaced(top: Int = 14) = LinearLayout.LayoutParams(MATCH_PARENT, WRAP_CONTENT).apply { topMargin = dp(top) }

    private fun weight(startMargin: Int = 0) =
        LinearLayout.LayoutParams(0, WRAP_CONTENT, 1f).apply { marginStart = dp(startMargin) }

    private fun dp(v: Int) = (v * resources.displayMetrics.density).toInt()

    private companion object {
        const val BG = 0xFF040711.toInt()
        const val PANEL = 0xF20B1221.toInt()
        const val BORDER = 0x3300F0FF
        const val TEXT = 0xFFE2E8F0.toInt()
        const val MUTED = 0xFF94A3B8.toInt()
        const val CYAN = 0xFF00F0FF.toInt()
        const val EMERALD = 0xFF00FF94.toInt()
        const val AMBER = 0xFFFFB800.toInt()
        const val PURPLE = 0xFFA066FF.toInt()
        const val CRIMSON = 0xFFFF3D71.toInt()
    }
}
