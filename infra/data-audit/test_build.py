"""Fail closed on a substituted base or native package before running Docker."""

import unittest

import build


class FrozenInputsTest(unittest.TestCase):
    def test_wrong_image_or_architecture_rejected(self):
        for changed in ({"Id": "sha256:other"}, {"Architecture": "amd64"}, {"Os": "other"}):
            image = {"Id": build.BASE_ID, "Architecture": "arm64", "Os": "linux"} | changed
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                build.check_base(image)

    def test_correct_base_accepted(self):
        build.check_base({"Id": build.BASE_ID, "Architecture": "arm64", "Os": "linux"})

    def test_corrupt_or_substituted_package_rejected(self):
        for payload in (b"", b"x" * build.PACKAGE_BYTES):
            with self.subTest(bytes=len(payload)), self.assertRaises(ValueError):
                build.check_package(payload)


if __name__ == "__main__":
    unittest.main()
