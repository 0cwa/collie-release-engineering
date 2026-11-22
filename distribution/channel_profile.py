"""Explicit identities and artifact names for independently trusted channels."""
from dataclasses import dataclass


@dataclass(frozen=True)
class ChannelProfile:
    name: str
    package: str
    app_title: str
    tag_prefix: str
    apk_asset: str
    repository_url: str
    metadata_dir: str
    apk_certificate: str | None
    repository_certificate: str | None
    source_branch: str


STABLE = ChannelProfile(
    name='stable', package='im.collie.app', app_title='Collie',
    tag_prefix='v', apk_asset='collie.apk',
    repository_url='https://repo.collie.im/fdroid/repo/',
    metadata_dir='distribution/metadata',
    apk_certificate='f8cc0c0c052e5635c26fcd293c2b0af3e87d61ff7f2df3602a9af6e91f73c826',
    repository_certificate='b0c5dcbac2b65ada4e8f641910097fb0bf86a5f7255319584d453c2932f5233c',
    source_branch='main',
)

# Beta APK signing awaits provisioning of its separate durable APK key. The repository
# index uses the already approved Collie repository key and public certificate.
BETA = ChannelProfile(
    name='trailblazer-beta', package='im.collie.app.trailblazer',
    app_title='Collie TrailBlazer Beta', tag_prefix='trailblazer-v',
    apk_asset='trailblazer-beta.apk',
    repository_url='https://repo.collie.im/fdroid/beta/repo/',
    metadata_dir='distribution/beta-metadata', apk_certificate='a515c03498bce8852b835544910b09ccc11a3abbed23e826e9c62c002680fe98',
    repository_certificate='b0c5dcbac2b65ada4e8f641910097fb0bf86a5f7255319584d453c2932f5233c', source_branch='trailblazer-beta',
)


def require_trusted(profile: ChannelProfile) -> None:
    if not profile.repository_certificate:
        raise ValueError(f'{profile.name} repository signer pin is not configured')


def require_apk_trusted(profile: ChannelProfile) -> None:
    if not profile.apk_certificate:
        raise ValueError(f'{profile.name} APK signer pin is not configured')
