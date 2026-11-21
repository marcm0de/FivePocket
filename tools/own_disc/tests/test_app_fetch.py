"""1.2.0 (owner: "outputs the correct apk and build file"): builder app fetches the edition's APK from the release page
download pinned in release.json (zip size and SHA-256, the APK inside and its SHA-256), once into the cache, and refuses a
download that is not the pinned file. Synthetic zips and a fake opener: nothing goes to the network.

    python3 -m unittest tools.own_disc.tests.test_app_fetch
"""
import hashlib
import io
import tempfile
import unittest
import urllib.error
import zipfile
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import builder as B  # noqa: E402

MEMBER = 'FivePocket-1.2.0-android/FivePocket-1.2.0.apk'
APK = b'PK\x03\x04 a synthetic apk'


def release_zip(apk=APK):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as z:
        z.writestr(MEMBER, apk)
        z.writestr('FivePocket-1.2.0-android/COPYING', b'licence')
    return buf.getvalue()


def release_for(data, apk=APK):
    return {'apps': {'fold': {'url': 'https://github.com/owner/FivePocket/releases/download/v1.2.0/FivePocket-1.2.0-android.zip',
                              'page': 'https://github.com/owner/FivePocket/releases/tag/v1.2.0', 'app': 'FivePocket single screen',
                              'sha256': hashlib.sha256(data).hexdigest(), 'size': len(data), 'member': MEMBER,
                              'member_sha256': hashlib.sha256(apk).hexdigest(), 'member_size': len(apk)}}}


class AppFetch(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.cache, self.out = self.root / 'cache', self.root / 'out'
        self.calls = []

    def tearDown(self):
        self.tmp.cleanup()

    def opener(self, data):
        def open_(url, timeout=None):
            self.calls.append(url)
            return io.BytesIO(data)
        return open_

    def test_the_pinned_apk_lands_beside_the_bundle_and_is_fetched_once(self):
        data = release_zip()
        release = release_for(data)
        dest = B.fetch_app(release, self.cache, 'fold', self.out, opener=self.opener(data))
        self.assertEqual((dest, dest.read_bytes()), (self.out / 'FivePocket-1.2.0.apk', APK))
        dest.unlink()
        def offline(url, timeout=None):
            raise urllib.error.URLError('no network')
        B.fetch_app(release, self.cache, 'fold', self.out, opener=offline)       # from the cache: no second download
        self.assertEqual((len(self.calls), dest.read_bytes()), (1, APK))

    def test_a_download_that_is_not_the_pinned_file_is_refused_and_nothing_is_kept(self):
        data = release_zip()
        for label, release, served in (('zip hash', release_for(release_zip(b'another apk')), data),
                                       ('apk hash', release_for(data, apk=b'the apk the pin names'), data)):
            with self.subTest(label), self.assertRaises(B.AppNotFetched):
                B.fetch_app(release, self.root / label / 'cache', 'fold', self.root / label / 'out', opener=self.opener(served))
            self.assertFalse((self.root / label / 'out' / 'FivePocket-1.2.0.apk').exists())
        self.assertEqual(list((self.root / 'zip hash' / 'cache' / 'apps').iterdir()), [], 'a refused download is not cached')

    def test_offline_says_what_to_download_and_from_where(self):
        release = release_for(release_zip())
        def offline(url, timeout=None):
            raise urllib.error.URLError('no network')
        with self.assertRaises(B.AppNotFetched) as cm:
            B.fetch_app(release, self.cache, 'fold', self.out, opener=offline)
        self.assertIn('could not download', str(cm.exception))
        advice = B.app_advice(release['apps']['fold'])
        self.assertIn('FivePocket-1.2.0-android.zip from https://github.com/owner/FivePocket/releases/tag/v1.2.0', advice)
        self.assertIn('FivePocket-1.2.0.apk', advice)

    def test_a_pin_that_is_not_a_release_page_download_refuses_the_release(self):
        release = release_for(release_zip())
        release['apps']['fold']['url'] = 'https://example.com/FivePocket-1.2.0-android.zip'
        with self.assertRaises(B.Refusal):
            B.apps_spec(release)


if __name__ == '__main__':
    unittest.main()
