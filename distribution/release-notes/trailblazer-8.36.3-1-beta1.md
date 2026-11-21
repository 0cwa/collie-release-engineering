## Collie TrailBlazer Beta 8.36.3-1-beta1

- **First TrailBlazer Beta.** TrailBlazer installs as its own app (`im.collie.app.trailblazer`) next to Collie Stable and is signed with a separate Beta key.
- **Updated to Signal 8.36.3**, the same base as Collie 8.36.3-1 RC1, including everything in that release candidate.
- **Molly 8.34 merged.** Background jobs wait for the passphrase lock, a protected install never gets a new database or attachment secret, and Molly's TLS trust fix is included.
- **Networking follows Molly.** Proxy selection and a full network reset when the proxy changes. Collie now connects on networks Android has not validated yet; a configured proxy is still enforced.
- **Privacy settings.** Molly's privacy settings screen, with Collie's network section kept inline.

SHA-256 of `trailblazer-beta.apk`: `9bb42b7213da1b16e4a5d868c66dc68cc97d38f0fe8f420ccf640bb11dcf22a8`
Signing certificate SHA-256: `a515c03498bce8852b835544910b09ccc11a3abbed23e826e9c62c002680fe98`
Built by [build-release run 44948699471](https://github.com/0cwa/collieim-android/actions/runs/44948699471) from `12a9f65656aef13ed319b79f5835baf8a426767c`.
