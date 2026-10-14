// Where the owner's data key is, and the one rule for using it to lock or seal (PR-14.9). The
// key is cached on this device and kept in the cloud, locked with his recovery code. The app
// makes a new key only for a vault that has none anywhere: a second key would open nothing he
// kept, and secrets locked with it could not be opened by his recipient.
import { api } from "../../api";
import { createOwnerDataKey, hasOwnerDataKey, type KeyBackup, ownerDataKey } from "../../vaultCrypto";

/** here: on this device. inCloud: only in the cloud, so the recovery code brings it here.
 *  elsewhere: in another browser, never kept in the cloud. none: no vault key yet. */
export type KeyPlace = "here" | "inCloud" | "elsewhere" | "none";

export function keyPlace(backup: KeyBackup | null, itemCount: number, sealed: boolean): KeyPlace {
  if (hasOwnerDataKey()) return "here";
  if (backup) return "inCloud";
  return itemCount || sealed ? "elsewhere" : "none";
}

export const KEY_NOT_HERE: Record<"inCloud" | "elsewhere", string> = {
  inCloud: "Enter your recovery code on the Vault tab first, to bring your key to this phone.",
  elsewhere: "Your vault was locked in another browser, and its key isn't kept in the cloud yet. Open the Vault tab there to make a recovery code.",
};

/** The key to lock or seal with: this device's, or a new one for a vault with no key anywhere. */
export async function keyForLocking(): Promise<CryptoKey> {
  const key = await ownerDataKey();
  if (key) return key;
  const [backup, { items }, sealed] = await Promise.all([
    api.ownerKeyBackup(),
    api.vaultItems(),
    api.getRecipient().then((r) => r.vault_sealed).catch((e) => {
      if (/no vault recipient/.test(String(e?.message))) return false;
      throw e;
    }),
  ]);
  const place = keyPlace(backup, items.length, sealed);
  if (place === "inCloud" || place === "elsewhere") throw new Error(KEY_NOT_HERE[place]);
  return createOwnerDataKey();
}
