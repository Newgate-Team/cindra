"""Cindra's live avatar streamer.

A long-running process (not a Celery task: it holds a chat connection,
an avatar session and a video encoder for hours): reads the channel's
chat, asks the Cindra API what the avatar should say, has the avatar
say it, and keeps the result streaming to the platform over RTMP.
"""
