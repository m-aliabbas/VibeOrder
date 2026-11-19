from django.contrib import admin
from django.urls import include, path

from apps.cash import views as cash
from apps.cloud import views as cloud
from apps.reports import views as reports
from apps.catalog import views as catalog
from apps.core import views as core
from apps.fulfillment import views as fulfillment
from apps.stock import views as stock
from apps.kitchen import views as kitchen
from apps.orders import views as orders

branch = [
    path("staff-directory", core.staff_directory),
    path("sessions", core.login),
    path("sessions/logout", core.logout),
    path("bootstrap", core.bootstrap),
    path("pairing-codes", core.create_pairing_code),
    path("devices/<uuid:device_id>/revoke", core.revoke_device),
    path("devices/health", kitchen.devices_health),
    path("recovery", core.recovery),
    path("menu", catalog.menu),
    path("availability", catalog.set_availability),
    path("floor", orders.floor),
    path("checks", orders.checks),
    path("checks/<uuid:check_id>", orders.check_detail),
    path("checks/<uuid:check_id>/rounds", orders.rounds),
    path("checks/<uuid:check_id>/fire", orders.fire),
    path("checks/<uuid:check_id>/request-bill", orders.request_bill),
    path("checks/<uuid:check_id>/amendments", orders.amendments),
    path("checks/<uuid:check_id>/void", orders.void),
    path("checks/<uuid:check_id>/move", orders.move),
    path("checks/<uuid:check_id>/merge", orders.merge),
    path("checks/<uuid:check_id>/tenders", cash.tenders),
    path("checks/<uuid:check_id>/close", cash.close_check),
    path("tenders/<uuid:tender_id>/refunds", cash.refunds),
    path("tenders/<uuid:tender_id>/receipt", cash.payer_receipt),
    path("refunds/<uuid:refund_id>/resolve", cash.resolve_refund),
    path("quotes", orders.quotes),
    path("stations/<uuid:station_id>/queue", kitchen.station_queue),
    path("ticket-items/<uuid:item_id>/transitions", kitchen.transition),
    path("tickets/<uuid:ticket_id>/acknowledge", kitchen.acknowledge),
    path("print-jobs", kitchen.print_jobs),
    path("print-jobs/<uuid:job_id>", kitchen.print_job_detail),
    path("print-jobs/<uuid:job_id>/reprints", kitchen.reprint),
    path("shifts", cash.shifts),
    path("shifts/<uuid:shift_id>", cash.shift_detail),
    path("shifts/<uuid:shift_id>/count", cash.count_shift),
    path("shifts/<uuid:shift_id>/reopen", cash.reopen_shift),
    path("shifts/<uuid:shift_id>/close", cash.close_shift),
    path("shifts/<uuid:shift_id>/movements", cash.movement),
    path("checks/<uuid:check_id>/discounts", orders.discounts),
    path("checks/<uuid:check_id>/discounts/<uuid:discount_id>/remove", orders.remove_discount),
    path("checks/<uuid:check_id>/handover", fulfillment.handover),
    path("zones", fulfillment.zones),
    path("customers", fulfillment.customers),
    path("customers/<uuid:customer_id>/repeat", fulfillment.repeat),
    path("packing", fulfillment.packing),
    path("deliveries", fulfillment.deliveries),
    path("deliveries/<uuid:delivery_id>/transitions", fulfillment.delivery_transition),
    path("deliveries/<uuid:delivery_id>/retry", fulfillment.delivery_retry),
    path("riders", fulfillment.riders),
    path("riders/<uuid:rider_id>/remittances", fulfillment.remittances),
    path("stock", stock.levels),
    path("stock/movements", stock.movements),
    path("stock/receipts", stock.receive),
    path("stock/losses", stock.loss),
    path("stock/returns", stock.returns),
    path("stock/counts", stock.count),
    path("stock/transfers", stock.transfer),
    path("stock/transfers/<uuid:transfer_id>/receive", stock.receive_transfer),
    path("reports/<str:name>", reports.report),
]

cloud_api = [
    path("cloud/sync/events", cloud.sync_events),
    path("cloud/sync/inbox", cloud.sync_inbox),
    path("cloud/sync/ack", cloud.sync_ack),
    path("store/<slug:slug>", cloud.store),
    path("store/<slug:slug>/<str:code>/menu", cloud.store_menu),
    path("store/<slug:slug>/<str:code>/quotes", cloud.store_quote),
    path("store/<slug:slug>/<str:code>/orders", cloud.store_order),
    path("track/<str:token>", cloud.track),
    path("payments/fakepay/webhook", cloud.fakepay_webhook),
    path("payments/fakepay/simulate", cloud.fakepay_simulate),
    path("owner/sessions", cloud.owner_login),
    path("owner/overview", cloud.owner_overview),
    path("owner/releases", cloud.owner_releases),
    path("owner/releases/<uuid:release_id>/preview", cloud.owner_preview),
    path("owner/releases/<uuid:release_id>/publish", cloud.owner_publish),
    path("owner/branches/<str:branch_id>/intake", cloud.owner_intake),
    path("owner/reports/<str:name>", cloud.owner_report),
]

urlpatterns = [
    path("admin/", admin.site.urls),
    path("healthz", core.healthz),
    path("readyz", core.readyz),
    path("kds/", kitchen.kds_page),
    path("api/v1/pair", core.pair_device),
    path("api/v1/", include(cloud_api)),
    path("s/<slug:slug>/", cloud.storefront_page),
    path("s/<slug:slug>/<str:code>/", cloud.storefront_page),
    path("t/<str:token>", cloud.track_page),
    path("owner/", cloud.owner_page),
    path("api/v1/branches/<uuid:branch_id>/", include(branch)),
]
