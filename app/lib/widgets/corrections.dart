import 'package:flutter/material.dart';

import '../api/api_client.dart';
import 'common.dart';

/// Reason picker with quick choices; returns null when cancelled.
Future<String?> askReason(BuildContext context, String title, {List<String> quick = const []}) {
  final c = TextEditingController();
  return showDialog<String>(
    context: context,
    builder: (ctx) => AlertDialog(
      title: Text(title),
      content: SizedBox(
        width: 420,
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            if (quick.isNotEmpty)
              Wrap(
                spacing: 8,
                runSpacing: 8,
                children: [for (final q in quick) ActionChip(label: Text(q), onPressed: () => Navigator.pop(ctx, q))],
              ),
            const SizedBox(height: 12),
            TextField(
              key: const ValueKey('reason'),
              controller: c,
              autofocus: quick.isEmpty,
              decoration: const InputDecoration(labelText: 'Reason'),
              onSubmitted: (v) => Navigator.pop(ctx, v.trim().isEmpty ? null : v.trim()),
            ),
          ],
        ),
      ),
      actions: [
        TextButton(onPressed: () => Navigator.pop(ctx), child: const Text('Cancel')),
        FilledButton(
          key: const ValueKey('reason-ok'),
          onPressed: () => Navigator.pop(ctx, c.text.trim().isEmpty ? null : c.text.trim()),
          child: const Text('OK'),
        ),
      ],
    ),
  );
}

/// Manager username + PIN, entered on this device. The manager acts with their own identity.
Future<Map<String, String>?> askApproval(BuildContext context, String why) {
  final user = TextEditingController();
  final pin = TextEditingController();
  return showDialog<Map<String, String>>(
    context: context,
    builder: (ctx) => AlertDialog(
      title: const Text('Manager approval'),
      content: SizedBox(
        width: 380,
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Text(why),
            const SizedBox(height: 12),
            TextField(
              key: const ValueKey('approver'),
              controller: user,
              autofocus: true,
              decoration: const InputDecoration(labelText: 'Manager username'),
            ),
            const SizedBox(height: 8),
            TextField(
              key: const ValueKey('approver-pin'),
              controller: pin,
              obscureText: true,
              keyboardType: TextInputType.number,
              decoration: const InputDecoration(labelText: 'Manager PIN'),
            ),
          ],
        ),
      ),
      actions: [
        TextButton(onPressed: () => Navigator.pop(ctx), child: const Text('Cancel')),
        FilledButton(
          key: const ValueKey('approve'),
          onPressed: () => Navigator.pop(ctx, {'username': user.text.trim(), 'pin': pin.text}),
          child: const Text('Approve'),
        ),
      ],
    ),
  );
}

Future<String?> askFoodResolution(BuildContext context, List<String> prepared) {
  return showDialog<String>(
    context: context,
    builder: (ctx) => AlertDialog(
      title: const Text('Food already prepared'),
      content: Text('${prepared.join('\n')}\n\nWhat happens to it?'),
      actions: [
        TextButton(onPressed: () => Navigator.pop(ctx), child: const Text('Cancel')),
        TextButton(onPressed: () => Navigator.pop(ctx, 'staff_meal'), child: const Text('Staff meal')),
        FilledButton(onPressed: () => Navigator.pop(ctx, 'discard'), child: const Text('Discard')),
      ],
    ),
  );
}

/// Runs a correction command. A rejected command wrote nothing, so asking for approval (or food
/// resolution) and resending with a fresh key is safe. An uncertain outcome keeps the same key.
Future<ApiResult?> runCorrection(BuildContext context, ApiClient api, String path, Map<String, dynamic> body) async {
  var payload = {...body};
  var key = ApiClient.newKey();
  for (var attempt = 0; attempt < 4; attempt++) {
    try {
      return await api.command(path, payload, key: key);
    } on ApiError catch (e) {
      if (!context.mounted) return null;
      if (e.uncertain) {
        final retry = await showDialog<bool>(
          context: context,
          builder: (ctx) => AlertDialog(
            title: const Text('Not confirmed'),
            content: Text('${e.message}\nRetrying is safe: it will not apply the correction twice.'),
            actions: [
              TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('Later')),
              FilledButton(onPressed: () => Navigator.pop(ctx, true), child: const Text('Retry')),
            ],
          ),
        );
        if (retry != true) return null;
        continue; // same key
      }
      if (e.code == 'approval_required' || e.code == 'approval_rejected') {
        final a = await askApproval(
          context,
          e.code == 'approval_rejected' ? 'Approval rejected. Try again.' : e.message,
        );
        if (a == null) return null;
        payload = {...payload, 'approval': a};
        key = ApiClient.newKey();
        continue;
      }
      if (e.code == 'food_resolution_required') {
        final r = await askFoodResolution(context, List<String>.from(e.body['prepared_items'] ?? const []));
        if (r == null) return null;
        payload = {...payload, 'food_resolution': r};
        key = ApiClient.newKey();
        continue;
      }
      showError(context, e);
      return null;
    }
  }
  return null;
}
