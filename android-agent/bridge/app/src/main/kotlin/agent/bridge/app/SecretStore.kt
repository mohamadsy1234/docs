package agent.bridge.app

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import java.security.KeyStore
import java.security.SecureRandom
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/**
 * API_SECRET (256-bit), wrapped with an AES-GCM key that never leaves the
 * Android Keystore (mvp-1-spec 2.1). EncryptedSharedPreferences is not used:
 * it is deprecated in androidx.security-crypto.
 */
class SecretStore(context: Context) {
    private val prefs = context.getSharedPreferences("bridge_secret", Context.MODE_PRIVATE)

    fun getOrCreate(): ByteArray {
        prefs.getString(KEY_BLOB, null)?.let { return unwrap(it) }
        val secret = ByteArray(32).also { SecureRandom().nextBytes(it) }
        prefs.edit().putString(KEY_BLOB, wrap(secret)).apply()
        return secret
    }

    private fun wrappingKey(): SecretKey {
        val ks = KeyStore.getInstance(KEYSTORE).apply { load(null) }
        (ks.getKey(ALIAS, null) as? SecretKey)?.let { return it }
        val spec = KeyGenParameterSpec.Builder(ALIAS, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
            .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
            .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
            .setKeySize(256)
            .build()
        return KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, KEYSTORE).apply { init(spec) }.generateKey()
    }

    private fun wrap(secret: ByteArray): String {
        val cipher = Cipher.getInstance(TRANSFORM).apply { init(Cipher.ENCRYPT_MODE, wrappingKey()) }
        val ct = cipher.doFinal(secret)
        return b64(cipher.iv) + ":" + b64(ct)
    }

    private fun unwrap(blob: String): ByteArray {
        val (iv, ct) = blob.split(":").map { Base64.decode(it, Base64.NO_WRAP) }
        val cipher = Cipher.getInstance(TRANSFORM).apply {
            init(Cipher.DECRYPT_MODE, wrappingKey(), GCMParameterSpec(128, iv))
        }
        return cipher.doFinal(ct)
    }

    private fun b64(bytes: ByteArray) = Base64.encodeToString(bytes, Base64.NO_WRAP)

    private companion object {
        const val KEYSTORE = "AndroidKeyStore"
        const val ALIAS = "agent_bridge_secret_wrap"
        const val TRANSFORM = "AES/GCM/NoPadding"
        const val KEY_BLOB = "secret_wrapped"
    }
}
