from pathlib import Path
import base64
import json
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "addons/rookframe_development"))
from rookframe_media import edit, project_media, assets

PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a5xkAAAAASUVORK5CYII=')

class MediaTests(unittest.TestCase):
    def test_attach_reorder_remove_preserves_bytes_and_derives_release_urls(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'rookframe.json').write_text(json.dumps({'manifestVersion': 1, 'download': 'https://example.test/v1/pack.rookpackage'}))
            image, video = root / 'input.png', root / 'film.mp4'
            image.write_bytes(PNG)
            video.write_bytes(b'\0\0\0\x18ftypisom0000')
            edit(root, image, 'cover', 'Cover')
            edit(root, video, 'gallery', 'Trailer')
            manifest = json.loads((root / 'rookframe.json').read_text())
            self.assertEqual(manifest['manifestVersion'], 2)
            media = manifest['media']
            self.assertNotIn('url', media['cover'])  # Derive from the current release at publication.
            self.assertEqual(project_media(root, manifest)[media['cover']['path']], PNG)
            self.assertEqual(project_media(root, manifest)[media['gallery'][0]['path']], video.read_bytes())
            edit(root, None, 'gallery', remove=media['gallery'][0]['path'], move=-1)
            with self.assertRaisesRegex(ValueError, 'gallery item'):
                edit(root, None, 'gallery', remove=media['cover']['path'], move=1)
            edit(root, None, 'cover', remove=media['cover']['path'])
            self.assertEqual(len(assets(json.loads((root / 'rookframe.json').read_text()))), 1)

    def test_bad_format_leaves_manifest_unchanged_and_outside_paths_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = '{"manifestVersion": 1}'
            (root / 'rookframe.json').write_text(original)
            image = root / 'input.png'
            image.write_bytes(b'not an image at all')
            with self.assertRaises(ValueError): edit(root, image, 'cover')
            with self.assertRaisesRegex(ValueError, "portable"):
                assets({"manifestVersion": 2, "media": {"cover": {"type": "image", "path": "media/../outside.png"}}})
            self.assertEqual((root / 'rookframe.json').read_text(), original)
