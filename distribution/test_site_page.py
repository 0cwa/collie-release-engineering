import json,tempfile,unittest
from pathlib import Path
from PIL import Image
import site_page as s
class SitePageTest(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup);self.root=Path(t.name)
    def render(self,state):return s.render(self.root/state,'https://repo.collie.im/fdroid/repo/','a'*64,'b'*64,'https://github.com/0cwa/collieim-android/releases/download/v8.35.1-1-rc1/collie.apk',state=state)
    def test_deeplink_and_manual_universal_fallbacks_bind_fingerprint(self):
        urls=s.links('https://repo.collie.im/fdroid/repo/','a'*64)
        self.assertEqual(urls['deep'],'fdroidrepos://repo.collie.im/fdroid/repo/?fingerprint='+'A'*64)
        self.assertEqual(urls['universal'],'https://fdroid.link/#'+urls['https'])
        for url in ('http://repo.collie.im/fdroid/repo/','https://secret@repo.collie.im/fdroid/repo/','https://repo.collie.im/?other=1'):
            with self.assertRaises(ValueError):s.links(url,'a'*64)
    def test_preview_qr_is_opaque_black_white_with_quiet_zone(self):
        self.render('preview');png=Image.open(self.root/'preview/add-repository.png');self.assertEqual(png.mode,'RGB');self.assertEqual(png.getpixel((0,0)),(255,255,255));self.assertEqual(set(png.get_flattened_data()),{(0,0,0),(255,255,255)})
        for x in range(png.width):self.assertEqual(png.getpixel((x,20)),(255,255,255))
        html=(self.root/'preview/index.html').read_text();self.assertIn('Local preview',html);self.assertIn('fdroidrepos:',html)
    def test_inactive_and_native_never_offer_add_link_or_qr(self):
        for state in ('inactive','native'):
            self.render(state);html=(self.root/state/'index.html').read_text();self.assertFalse(s.has_add_link(html));self.assertFalse((self.root/state/'add-repository.png').exists());self.assertNotIn('signed index',html)
        self.assertIn('Download APK',(self.root/'inactive/index.html').read_text())
    def test_pinned_font_and_icon_assets_have_licenses_and_no_remote_font_load(self):
        css=(s.ROOT/'assets/site.css').read_text();self.assertNotIn('https://',css);self.assertIn('font-weight:350',css);self.assertIn('font-weight:300',css)
        for font in ('manrope','sora'):self.assertIn('SIL OPEN FONT LICENSE',(s.ROOT/'assets'/f'{font}-OFL.txt').read_text())
        self.assertIn('color-scheme:only light',css);self.assertIn('forced-color-adjust:none',css)

    def test_add_link_detection_covers_browser_case_and_whitespace(self):
        self.assertTrue(s.has_add_link('<a href="  FDROIDREPOS://repo.collie.im/fdroid/repo/">Add</a>'))
        self.assertFalse(s.has_add_link('<a href="https://github.com/0cwa/collieim-android">Source</a>'))

    def test_relative_repository_links_resolve_against_approved_origin(self):
        for href in ('/fdroid/repo/?fingerprint='+'A'*64,'//repo.collie.im/fdroid/repo/?fingerprint='+'A'*64):
            with self.subTest(href=href):self.assertTrue(s.has_add_link('<a href="'+href+'">Add</a>'))
        self.assertFalse(s.has_add_link('<a href="/fdroid/repository-guide">Docs</a>'))
        self.assertFalse(s.has_add_link('<a href="//example.org/fdroid/repo/">Other</a>'))
    def test_reused_inactive_native_output_rejects_stale_qr_without_deletion(self):
        self.render('preview');out=self.root/'preview';png=(out/'add-repository.png').read_bytes();html=(out/'index.html').read_bytes()
        for state in ('inactive','native'):
            with self.assertRaisesRegex(ValueError,'existing QR'):s.render(out,'https://repo.collie.im/fdroid/repo/','a'*64,'b'*64,'https://github.com/0cwa/collieim-android/releases/download/v1/collie.apk',state=state)
            self.assertEqual((out/'add-repository.png').read_bytes(),png);self.assertEqual((out/'index.html').read_bytes(),html)
    def test_displayed_manual_address_includes_full_pinned_fingerprint(self):
        self.render('preview');text=(self.root/'preview/index.html').read_text();pinned=s.links('https://repo.collie.im/fdroid/repo/','a'*64)['https']
        self.assertIn('>'+pinned+'</a>',text)
