"""Expose upstream monitor-only operations under native API authentication."""


def install_monitor_controls(api, application):
    @api.post('/api/v1/tasks/{room_id}/monitor/enable')
    async def enable(room_id: int):
        await application.enable_task_monitor(room_id)
        return {'code': 0, 'message': 'Monitor enabled'}

    @api.post('/api/v1/tasks/{room_id}/monitor/disable')
    async def disable(room_id: int):
        await application.disable_task_monitor(room_id)
        return {'code': 0, 'message': 'Monitor disabled'}

    # Upstream mounts the native SPA at '/'. Register with api.post so the
    # existing API-key dependencies apply, then put these routes before it.
    routes = api.router.routes
    new_routes = routes[-2:]
    del routes[-2:]
    mount = next((i for i, route in enumerate(routes) if route.path in ('', '/')), len(routes))
    routes[mount:mount] = new_routes
