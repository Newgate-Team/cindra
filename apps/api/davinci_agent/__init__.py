"""Cindra's DaVinci Resolve montage agent.

Runs on the machine where DaVinci Resolve Studio is installed: takes
montage jobs from the Cindra API, assembles them in Resolve and uploads
the rendered video back. Standard library only -- the render machine
needs nothing installed besides Python 3.10+ and Resolve Studio
(external scripting is Studio-only since Resolve 19.1).
"""
