import 'package:flutter/material.dart';

import '../api/api_client.dart';
import '../models/inbox_item.dart';
import '../models/user_profile.dart';
import '../storage/session_storage.dart';
import 'login_screen.dart';

class MemberScreen extends StatefulWidget {
  final ApiClient client;
  final UserProfile userProfile;

  const MemberScreen({
    super.key,
    required this.client,
    required this.userProfile,
  });

  @override
  State<MemberScreen> createState() => MemberScreenState();
}

class MemberScreenState extends State<MemberScreen> {
  final List<InboxItem> _items = [];
  String? _nextCursor;
  bool _isLoading = false;
  bool _isLoadingMore = false;
  String? _errorMessage;

  @override
  void initState() {
    super.initState();
    _loadInitialInbox();
  }

  Future<void> _loadInitialInbox() async {
    setState(() {
      _isLoading = true;
      _errorMessage = null;
    });

    try {
      final paginated = await widget.client.getInbox();
      if (!mounted) return;

      setState(() {
        _items.clear();
        _items.addAll(paginated.items);
        _nextCursor = paginated.nextCursor;
        _isLoading = false;
      });

      await _sendBatchDeliveredReceipts(paginated.items);
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _errorMessage = e.toString();
        _isLoading = false;
      });
    }
  }

  Future<void> _refreshInbox() async {
    try {
      final paginated = await widget.client.getInbox();
      if (!mounted) return;

      setState(() {
        _items.clear();
        _items.addAll(paginated.items);
        _nextCursor = paginated.nextCursor;
        _errorMessage = null;
      });

      await _sendBatchDeliveredReceipts(paginated.items);
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _errorMessage = e.toString();
      });
    }
  }

  Future<void> _loadMore() async {
    if (_nextCursor == null || _isLoadingMore) return;

    setState(() => _isLoadingMore = true);

    try {
      final paginated = await widget.client.getInbox(cursorUrl: _nextCursor);
      if (!mounted) return;

      setState(() {
        _items.addAll(paginated.items);
        _nextCursor = paginated.nextCursor;
        _isLoadingMore = false;
      });

      await _sendBatchDeliveredReceipts(paginated.items);
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _errorMessage = e.toString();
        _isLoadingMore = false;
      });
    }
  }

  Future<void> _sendBatchDeliveredReceipts(List<InboxItem> items) async {
    final undeliveredIds = items
        .where((i) => !i.isDelivered)
        .map((i) => i.announcementId)
        .toSet()
        .toList();

    if (undeliveredIds.isEmpty) return;

    try {
      await widget.client.sendReceipts(undeliveredIds, kind: 'delivered');
    } catch (_) {
      // Don't crash on receipt failure
    }
  }

  void _openItemDetail(InboxItem item) {
    // Opening sends receipt of kind "read"
    if (!item.isRead) {
      widget.client.sendReceipts([item.announcementId], kind: 'read').catchError((_) => 0);
    }

    showModalBottomSheet(
      context: context,
      isScrollControlled: true,
      builder: (ctx) => _ItemDetailSheet(
        client: widget.client,
        item: item,
        onUpdated: () => setState(() {}),
      ),
    );
  }

  void _handleLogout() {
    SessionStorage.instance.clearMemberSession();
    widget.client.logout();
    Navigator.of(context).pushReplacement(
      MaterialPageRoute(
        builder: (_) => LoginScreen(client: widget.client),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: Text('CrewLink Inbox (${widget.userProfile.localName})'),
        actions: [
          IconButton(
            key: const Key('refresh_inbox_button'),
            icon: const Icon(Icons.refresh),
            tooltip: 'Refresh Inbox',
            onPressed: _isLoading ? null : _refreshInbox,
          ),
          IconButton(
            key: const Key('logout_button'),
            icon: const Icon(Icons.logout),
            tooltip: 'Log Out',
            onPressed: _handleLogout,
          ),
        ],
      ),
      body: _isLoading
          ? const Center(child: CircularProgressIndicator())
          : RefreshIndicator(
              onRefresh: _refreshInbox,
              child: ListView(
                physics: const AlwaysScrollableScrollPhysics(),
                padding: const EdgeInsets.all(12),
                children: [
                  if (_errorMessage != null) ...[
                    Container(
                      padding: const EdgeInsets.all(12),
                      margin: const EdgeInsets.only(bottom: 12),
                      decoration: BoxDecoration(
                        color: Colors.red.shade100,
                        borderRadius: BorderRadius.circular(4),
                        border: Border.all(color: Colors.red.shade400),
                      ),
                      child: Text(
                        _errorMessage!,
                        style: TextStyle(color: Colors.red.shade900),
                      ),
                    ),
                  ],
                  if (_items.isEmpty)
                    const Padding(
                      padding: EdgeInsets.symmetric(vertical: 40),
                      child: Center(child: Text('No announcements in your inbox.')),
                    )
                  else
                    ..._items.map((item) {
                      return Card(
                        key: Key('inbox_item_${item.announcementId}'),
                        margin: const EdgeInsets.symmetric(vertical: 6),
                        child: ListTile(
                          title: Text(
                            item.announcementTitle,
                            style: TextStyle(
                              fontWeight: item.isRead ? FontWeight.normal : FontWeight.bold,
                            ),
                          ),
                          subtitle: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              Text(
                                item.announcementBody,
                                maxLines: 2,
                                overflow: TextOverflow.ellipsis,
                              ),
                              const SizedBox(height: 4),
                              Row(
                                children: [
                                  if (item.hasMeetingTime)
                                    Padding(
                                      padding: const EdgeInsets.only(right: 8.0),
                                      child: Text(
                                        'Meeting: ${item.announcementStartsAt}',
                                        style: TextStyle(fontSize: 12, color: Colors.blue.shade700),
                                      ),
                                    ),
                                  if (item.response != null)
                                    Chip(
                                      label: Text(
                                        item.response == 'coming' ? 'Coming' : "Can't make it",
                                        style: const TextStyle(fontSize: 10),
                                      ),
                                      padding: EdgeInsets.zero,
                                      visualDensity: VisualDensity.compact,
                                    )
                                  else if (item.isAcknowledged)
                                    const Chip(
                                      label: Text('Acknowledged', style: TextStyle(fontSize: 10)),
                                      padding: EdgeInsets.zero,
                                      visualDensity: VisualDensity.compact,
                                    ),
                                ],
                              ),
                            ],
                          ),
                          trailing: const Icon(Icons.chevron_right),
                          onTap: () => _openItemDetail(item),
                        ),
                      );
                    }),
                  if (_nextCursor != null)
                    Padding(
                      padding: const EdgeInsets.symmetric(vertical: 16),
                      child: Center(
                        child: ElevatedButton(
                          key: const Key('load_more_button'),
                          onPressed: _isLoadingMore ? null : _loadMore,
                          child: _isLoadingMore
                              ? const SizedBox(
                                  width: 16,
                                  height: 16,
                                  child: CircularProgressIndicator(strokeWidth: 2),
                                )
                              : const Text('Load more'),
                        ),
                      ),
                    ),
                ],
              ),
            ),
    );
  }
}

class _ItemDetailSheet extends StatefulWidget {
  final ApiClient client;
  final InboxItem item;
  final VoidCallback onUpdated;

  const _ItemDetailSheet({
    required this.client,
    required this.item,
    required this.onUpdated,
  });

  @override
  State<_ItemDetailSheet> createState() => _ItemDetailSheetState();
}

class _ItemDetailSheetState extends State<_ItemDetailSheet> {
  bool _isActionInProgress = false;
  String? _closedError;

  Future<void> _handleAcknowledge() async {
    setState(() {
      _isActionInProgress = true;
      _closedError = null;
    });

    try {
      await widget.client.acknowledge(widget.item.announcementId);
      if (!mounted) return;
      setState(() {
        _isActionInProgress = false;
      });
      widget.item.response = widget.item.response; // keep RSVP if any
      widget.onUpdated();
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('Callout acknowledged!')),
      );
    } on ApiException catch (e) {
      if (!mounted) return;
      setState(() {
        _isActionInProgress = false;
        if (e.statusCode == 409 || e.errorCode == 'callout_closed') {
          _closedError = 'This callout has been closed.';
        } else {
          _closedError = e.message;
        }
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _isActionInProgress = false;
        _closedError = e.toString();
      });
    }
  }

  Future<void> _handleRespond(String responseValue) async {
    setState(() {
      _isActionInProgress = true;
      _closedError = null;
    });

    try {
      await widget.client.respond(widget.item.announcementId, responseValue);
      if (!mounted) return;
      setState(() {
        widget.item.response = responseValue;
        _isActionInProgress = false;
      });
      widget.onUpdated();
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text(
            responseValue == 'coming'
                ? "Response recorded: Coming"
                : "Response recorded: Can't make it",
          ),
        ),
      );
    } on ApiException catch (e) {
      if (!mounted) return;
      setState(() {
        _isActionInProgress = false;
        if (e.statusCode == 409 || e.errorCode == 'callout_closed') {
          _closedError = 'This callout has been closed.';
        } else {
          _closedError = e.message;
        }
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _isActionInProgress = false;
        _closedError = e.toString();
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final item = widget.item;

    return Padding(
      padding: EdgeInsets.only(
        left: 20,
        right: 20,
        top: 20,
        bottom: MediaQuery.of(context).viewInsets.bottom + 20,
      ),
      child: SingleChildScrollView(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          mainAxisSize: MainAxisSize.min,
          children: [
            Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              children: [
                Expanded(
                  child: Text(
                    item.announcementTitle,
                    style: const TextStyle(fontSize: 20, fontWeight: FontWeight.bold),
                  ),
                ),
                IconButton(
                  icon: const Icon(Icons.close),
                  onPressed: () => Navigator.of(context).pop(),
                ),
              ],
            ),
            const SizedBox(height: 8),
            if (item.hasMeetingTime) ...[
              Container(
                padding: const EdgeInsets.all(8),
                decoration: BoxDecoration(
                  color: Colors.blue.shade50,
                  borderRadius: BorderRadius.circular(4),
                ),
                child: Row(
                  children: [
                    const Icon(Icons.event, size: 20, color: Colors.blue),
                    const SizedBox(width: 8),
                    Text(
                      'Meeting: ${item.announcementStartsAt}',
                      style: TextStyle(fontWeight: FontWeight.w600, color: Colors.blue.shade900),
                    ),
                  ],
                ),
              ),
              const SizedBox(height: 12),
            ],
            Text(
              item.announcementBody,
              style: const TextStyle(fontSize: 16),
            ),
            const SizedBox(height: 16),
            if (_closedError != null) ...[
              Container(
                padding: const EdgeInsets.all(12),
                margin: const EdgeInsets.only(bottom: 12),
                decoration: BoxDecoration(
                  color: Colors.red.shade100,
                  borderRadius: BorderRadius.circular(4),
                  border: Border.all(color: Colors.red.shade400),
                ),
                child: Text(
                  _closedError!,
                  style: TextStyle(color: Colors.red.shade900, fontWeight: FontWeight.bold),
                ),
              ),
            ],
            if (item.response != null)
              Padding(
                padding: const EdgeInsets.only(bottom: 12.0),
                child: Text(
                  'Your answer: ${item.response == "coming" ? "Coming" : "Can't make it"}',
                  style: const TextStyle(fontWeight: FontWeight.bold, color: Colors.indigo),
                ),
              ),
            Wrap(
              spacing: 8,
              runSpacing: 8,
              children: [
                ElevatedButton(
                  key: const Key('got_it_button'),
                  onPressed: _isActionInProgress ? null : _handleAcknowledge,
                  child: const Text('Got it'),
                ),
                ElevatedButton(
                  key: const Key('coming_button'),
                  onPressed: _isActionInProgress ? null : () => _handleRespond('coming'),
                  style: ElevatedButton.styleFrom(backgroundColor: Colors.green.shade100),
                  child: Text('Coming', style: TextStyle(color: Colors.green.shade900)),
                ),
                ElevatedButton(
                  key: const Key('cant_make_it_button'),
                  onPressed: _isActionInProgress ? null : () => _handleRespond('cant_make_it'),
                  style: ElevatedButton.styleFrom(backgroundColor: Colors.orange.shade100),
                  child: Text("Can't make it", style: TextStyle(color: Colors.orange.shade900)),
                ),
              ],
            ),
            const SizedBox(height: 12),
          ],
        ),
      ),
    );
  }
}
