import json
import unittest

from scripts.publish_instagram import PublisherError, load_approved_post


def payload(**overrides):
    value = {
        "schema": 1,
        "approval_id": "PRA-20260817-001",
        "caption": "A positive aviation story.",
        "credit_line": "Photo courtesy of Example Aviation",
        "image_url": "https://example.com/post.jpg",
        "source_url": "https://example.com/story",
        "dry_run": True,
    }
    value.update(overrides)
    return json.dumps(value)


class ApprovedPostTests(unittest.TestCase):
    def test_credit_is_appended(self):
        post = load_approved_post(payload())
        self.assertIn("Photo courtesy of Example Aviation", post.caption)

    def test_existing_credit_is_not_duplicated(self):
        caption = "Story\n\nPhoto courtesy of Example Aviation"
        post = load_approved_post(payload(caption=caption))
        self.assertEqual(post.caption, caption)

    def test_rejects_non_https_image(self):
        with self.assertRaises(PublisherError):
            load_approved_post(payload(image_url="http://example.com/post.jpg"))

    def test_rejects_bad_schema(self):
        with self.assertRaises(PublisherError):
            load_approved_post(payload(schema=2))

    def test_rejects_overlong_final_caption(self):
        with self.assertRaises(PublisherError):
            load_approved_post(payload(caption="x" * 2_200))


if __name__ == "__main__":
    unittest.main()
