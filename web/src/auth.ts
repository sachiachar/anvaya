// Sign-in with Google (Firebase Authentication). The API checks the token and the
// demo allowlist on every request. Sign-in is on when the Firebase config is set.
import { initializeApp } from "firebase/app";
import { GoogleAuthProvider, getAuth, onAuthStateChanged, signInWithPopup, signOut, type User } from "firebase/auth";

const config = {
  apiKey: import.meta.env.VITE_FIREBASE_API_KEY,
  authDomain: import.meta.env.VITE_FIREBASE_AUTH_DOMAIN,
  projectId: import.meta.env.VITE_FIREBASE_PROJECT_ID,
  appId: import.meta.env.VITE_FIREBASE_APP_ID,
};

export const authEnabled = Boolean(config.apiKey);
const auth = authEnabled ? getAuth(initializeApp(config)) : null;

export function watchUser(callback: (user: User | null) => void): () => void {
  if (!auth) {
    callback(null);
    return () => {};
  }
  return onAuthStateChanged(auth, callback);
}

export async function signInWithGoogle() {
  if (auth) await signInWithPopup(auth, new GoogleAuthProvider());
}

export async function signOutUser() {
  if (auth) await signOut(auth);
}

/** The current user's ID token (refreshed automatically by Firebase), or null. */
export async function idToken(): Promise<string | null> {
  return auth?.currentUser ? auth.currentUser.getIdToken() : null;
}
