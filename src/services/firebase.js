import { initializeApp } from 'firebase/app'
import { getAuth, GoogleAuthProvider, signInWithPopup, signOut } from 'firebase/auth'

const firebaseConfig = {
  apiKey:            import.meta.env.VITE_FIREBASE_API_KEY,
  authDomain:        import.meta.env.VITE_FIREBASE_AUTH_DOMAIN,
  projectId:         import.meta.env.VITE_FIREBASE_PROJECT_ID,
  messagingSenderId: import.meta.env.VITE_FIREBASE_MESSAGING_SENDER_ID,
  appId:             import.meta.env.VITE_FIREBASE_APP_ID,
}

export const firebaseApp = initializeApp(firebaseConfig)
export const firebaseAuth = getAuth(firebaseApp)

const googleProvider = new GoogleAuthProvider()
googleProvider.setCustomParameters({ prompt: 'select_account' })

export async function googleSignIn() {
  const result = await signInWithPopup(firebaseAuth, googleProvider)
  const u = result.user
  return {
    googleId: u.uid,
    email: u.email,
    name: u.displayName || (u.email || '').split('@')[0],
    photoURL: u.photoURL || null,
  }
}

export async function googleSignOut() {
  try { await signOut(firebaseAuth) } catch { /* ignore */ }
}
