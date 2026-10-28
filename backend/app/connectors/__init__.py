"""Connectors to the places company files live: a shared folder, Google Drive, Microsoft 365."""

from ..models import Source, SourceKind
from .base import Connector, ConnectorError, DirectoryGroup, DirectoryUser, Grant, RemoteFile

__all__ = ["Connector", "ConnectorError", "DirectoryGroup", "DirectoryUser", "Grant", "RemoteFile", "build_connector"]


def build_connector(source: Source) -> Connector:
    kind = SourceKind(source.kind)
    if kind is SourceKind.FOLDER:
        from .folder import FolderConnector

        return FolderConnector(source.config or {})
    if kind is SourceKind.GOOGLE:
        from .google import GoogleDriveConnector

        return GoogleDriveConnector(source.config or {}, directory=source.sync_directory)
    from .microsoft import Microsoft365Connector

    return Microsoft365Connector(source.config or {}, directory=source.sync_directory)
