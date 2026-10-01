package agent.bridge.app

import agent.bridge.core.toHex
import android.Manifest
import android.app.Activity
import android.content.ClipData
import android.content.ClipDescription
import android.content.ClipboardManager
import android.content.Intent
import android.graphics.Typeface
import android.os.Build
import android.os.Bundle
import android.os.PersistableBundle
import android.provider.Settings
import android.widget.Button
import android.widget.LinearLayout
import android.widget.Switch
import android.widget.TextView
import android.widget.Toast

/** Pairing, consent and permissions (mvp-1-spec 2.1, 5). Deliberately minimal UI. */
class MainActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val prefs = Prefs(this)
        val secretHex = SecretStore(this).getOrCreate().toHex()

        val pad = (16 * resources.displayMetrics.density).toInt()
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(pad, pad, pad, pad)
        }

        root.addView(TextView(this).apply { text = getString(R.string.secret_label) })
        root.addView(TextView(this).apply {
            text = secretHex
            typeface = Typeface.MONOSPACE
            setTextIsSelectable(false)
        })
        root.addView(Button(this).apply {
            text = getString(R.string.copy)
            setOnClickListener {
                val clip = ClipData.newPlainText("API_SECRET", "API_SECRET=$secretHex")
                // Keep the secret out of clipboard previews (Android 13+).
                clip.description.extras = PersistableBundle().apply {
                    putBoolean(ClipDescription.EXTRA_IS_SENSITIVE, true)
                }
                getSystemService(ClipboardManager::class.java)?.setPrimaryClip(clip)
                Toast.makeText(this@MainActivity, R.string.copied, Toast.LENGTH_SHORT).show()
            }
        })

        root.addView(Switch(this).apply {
            text = getString(R.string.consent)
            isChecked = prefs.consent
            setOnCheckedChangeListener { _, checked -> prefs.consent = checked }
        })

        root.addView(Button(this).apply {
            text = getString(R.string.grant_listener)
            setOnClickListener { startActivity(Intent(Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS)) }
        })

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            root.addView(Button(this).apply {
                text = getString(R.string.grant_post)
                setOnClickListener { requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), 1) }
            })
        }

        setContentView(root)
    }
}
