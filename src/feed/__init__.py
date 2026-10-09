"""The public weekly feed of new food brands.

``build`` reads the database and decides what may be published (one filtering
function, :func:`src.feed.build.publishable`); ``render`` turns that into HTML,
Atom, RSS and JSON. The same functions serve the static build
(``python -m src.pipeline build-feed``) and the ``/feed`` routes of the website.
See docs/PUBLIC_FEED.md.
"""
