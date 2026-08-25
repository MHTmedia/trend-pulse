"""TrendPulse shared library — imported by both the Flask app and the nightly job.

Lives at repo root (not scripts/) because scripts/ is excluded by .vercelignore
and therefore unavailable to the deployed serverless function.
"""
