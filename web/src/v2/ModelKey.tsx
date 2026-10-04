import { useEffect, useState } from "react";
import { api, type KeyFeature, type KeyKind, type KeyResult, type ModelAccessState } from "../api";
import { useAction } from "../ui";
import { friendlyError } from "./copy";

const KIND_TEXT: Record<KeyKind, string> = { studio: "Google AI Studio", vertex_express: "Vertex AI (express mode)" };
const RESULT_TEXT: Record<KeyResult, string> = {
  works: "Works",
  not_available: "Not available with this key",
  out_of_quota: "Out of quota",
  refused: "Key refused",
  not_allowed: "Paused by Anvaya",
  error: "Didn't work",
};

function Results({ features }: { features: KeyFeature[] }) {
  return (
    <ul className="v2-key-results">
      {features.map((f) => (
        <li key={f.id} data-ok={f.result === "works"}>
          <span>{f.label}</span>
          <span>{RESULT_TEXT[f.result]}{f.result === "works" ? ` · ${(f.ms / 1000).toFixed(1)} s` : ""}</span>
        </li>
      ))}
    </ul>
  );
}

/** Your own model key, optional (PR-10.10): test it, save it, remove it. Everything you do in the
 *  demo then runs on your key first, and on Anvaya's only if yours fails. The key is never shown
 *  again after it's typed, to you or anyone else; the server sends back only its last 4 characters. */
export function ModelKey() {
  const [state, setState] = useState<ModelAccessState | null>(null);
  const [editing, setEditing] = useState(false);
  const [kind, setKind] = useState<KeyKind>("studio");
  const [key, setKey] = useState("");
  const [tested, setTested] = useState<{ features: KeyFeature[]; usable: boolean } | null>(null);
  const { busy, error, run } = useAction();

  useEffect(() => {
    api.modelAccess().then((s) => {
      setState(s);
      const allowed = s.kinds.filter((k) => s.policy.kinds.includes(k));
      if (allowed.length) setKind(allowed[0]);
    }).catch(() => setState(null));
  }, []);
  if (!state) return null;
  const { policy, access, ready } = state;
  const kinds = state.kinds.filter((k) => policy.kinds.includes(k));
  const done = () => { setKey(""); setTested(null); setEditing(false); };

  const test = () => run(async () => setTested(await api.testModelKey(kind, key.trim())));
  const save = () => run(async () => {
    const saved = await api.saveModelKey(kind, key.trim());
    setState({ ...state, access: saved.access });
    done();
  });
  const remove = () => run(async () => {
    await api.removeModelKey();
    setState({ ...state, access: null });
  });

  return (
    <section aria-labelledby="model-key">
      <p id="model-key" className="v2-eyebrow">Your own model key (optional)</p>
      <p className="v2-muted v2-small">
        Use your own Gemini key for everything you do in this demo. Anvaya's key is used only if yours fails.
        Nobody else can see or use your key, and it isn't shown again after you save it.
      </p>
      {!policy.enabled && <p className="v2-muted v2-small">Adding your own key is turned off for your account; Anvaya's key answers instead.</p>}
      {policy.enabled && !ready && <p className="v2-muted v2-small">Adding your own key isn't set up on this server yet.</p>}
      {/* A saved key can always be seen and removed, even when adding one is turned off. */}
      {(access || (policy.enabled && ready)) && (
        <>
          {access && !editing && (
            <div className="v2-stack">
              <p className="v2-small">
                <strong>{KIND_TEXT[access.kind]} {access.last4}</strong>
                {" · "}
                {access.status === "working" ? "Working" : "Failing: Anvaya's key is answering for now"}
              </p>
              {access.features?.length > 0 && <Results features={access.features} />}
              <div className="v2-row">
                {policy.enabled && ready && <button className="v2-btn quiet" disabled={busy} onClick={() => setEditing(true)}>Replace</button>}
                <button className="v2-btn quiet" disabled={busy} onClick={remove}>Remove</button>
              </div>
            </div>
          )}
          {(!access || editing) && policy.enabled && ready && (
            <form className="v2-stack" onSubmit={(e) => { e.preventDefault(); test(); }}>
              {kinds.length > 1 && (
                <fieldset className="v2-row v2-key-kinds">
                  <legend className="v2-small">Kind of key</legend>
                  {kinds.map((k) => (
                    <label key={k} className="v2-small">
                      <input type="radio" name="key-kind" checked={kind === k}
                        onChange={() => { setKind(k); setTested(null); }} /> {KIND_TEXT[k]}
                    </label>
                  ))}
                </fieldset>
              )}
              <div className="v2-field">
                <label htmlFor="v2-model-key">API key</label>
                <input id="v2-model-key" className="v2-input" type="password" value={key} autoComplete="off"
                  spellCheck={false} onChange={(e) => { setKey(e.target.value); setTested(null); }} />
              </div>
              {kind === "studio" && (
                <p className="v2-muted v2-small">
                  With a free AI Studio key, Google may use what you send to improve its products. Everything in
                  this demo is made up.
                </p>
              )}
              {tested && <Results features={tested.features} />}
              <div className="v2-row">
                <button className="v2-btn" type="submit" disabled={busy || !key.trim()}>Test</button>
                <button className="v2-btn" type="button" disabled={busy || !tested?.usable} onClick={save}>Save</button>
                {editing && <button className="v2-btn quiet" type="button" disabled={busy} onClick={done}>Cancel</button>}
              </div>
              {tested && !tested.usable && (
                <p className="v2-muted v2-small">This key can't reach any of the models Anvaya uses, so it can't be saved.</p>
              )}
            </form>
          )}
        </>
      )}
      {error && <p className="v2-error" role="alert">{friendlyError(new Error(error))}</p>}
    </section>
  );
}
