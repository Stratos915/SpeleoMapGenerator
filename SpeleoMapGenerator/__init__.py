def classFactory(iface):
    from .speleomap_plugin import SpeleoMapPlugin
    return SpeleoMapPlugin(iface)
