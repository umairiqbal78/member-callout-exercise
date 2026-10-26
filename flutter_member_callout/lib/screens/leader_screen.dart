import 'dart:async';
import 'package:flutter/material.dart';
import 'package:uuid/uuid.dart';

import '../api/api_client.dart';
import '../models/announcement.dart';
import '../models/user_profile.dart';
import '../storage/session_storage.dart';
import 'login_screen.dart';

class LeaderScreen extends StatefulWidget {
  final ApiClient client;
  final UserProfile userProfile;

  const LeaderScreen({
    super.key,
    required this.client,
    required this.userProfile,
  });

  @override
  State<LeaderScreen> createState() => LeaderScreenState();
}

class LeaderScreenState extends State<LeaderScreen> {
  final _uuid = const Uuid();

  // Local selection
  late String _selectedLocalId;
  late String _selectedLocalName;

  // Audience
  List<String> _availableClassifications = [];
  final Set<String> _selectedClassifications = {};
  bool _isLoadingClassifications = false;

  // Existing announcements
  List<AnnouncementItem> _existingAnnouncements = [];

  // Form Fields
  final _rawNoteController = TextEditingController();
  final _titleController = TextEditingController();
  final _bodyController = TextEditingController();
  final _pushPreviewController = TextEditingController();

  // AI & Approval state
  String? _aiIdempotencyKey;
  bool _isDraftingAI = false;
  List<String> _aiWarnings = [];
  String? _aiError;

  bool _isAiDraft = false;
  bool _isApproved = false;

  // Active Announcement state
  AnnouncementItem? _currentAnnouncement;
  AnnouncementStats? _currentStats;
  bool _isSavingDraft = false;
  bool _isApproving = false;
  bool _isSending = false;
  String? _actionMessage;
  String? _errorMessage;
  bool _isProgrammaticChange = false;

  // Scroll & Polling
  final _scrollController = ScrollController();
  Timer? _pollingTimer;
  bool _isPollingStats = false;

  @override
  void initState() {
    super.initState();
    final roles = widget.userProfile.leadershipRoles;
    if (roles.isNotEmpty) {
      _selectedLocalId = roles.first.localId.isNotEmpty
          ? roles.first.localId
          : widget.userProfile.localId;
      _selectedLocalName = roles.first.localName.isNotEmpty
          ? roles.first.localName
          : (widget.userProfile.localName.isNotEmpty
              ? widget.userProfile.localName
              : _selectedLocalId);
    } else {
      _selectedLocalId = widget.userProfile.localId;
      _selectedLocalName = widget.userProfile.localName.isNotEmpty
          ? widget.userProfile.localName
          : _selectedLocalId;
    }

    _aiIdempotencyKey = _uuid.v4();

    _rawNoteController.addListener(_onRawNoteChanged);
    _titleController.addListener(_onFieldEdited);
    _bodyController.addListener(_onFieldEdited);
    _pushPreviewController.addListener(_onPushPreviewEdited);

    _loadLocalData();
  }

  @override
  void dispose() {
    _pollingTimer?.cancel();
    _pollingTimer = null;
    _scrollController.dispose();
    _rawNoteController.dispose();
    _titleController.dispose();
    _bodyController.dispose();
    _pushPreviewController.dispose();
    super.dispose();
  }

  void _onRawNoteChanged() {
    // Generate new key when note changes
    _aiIdempotencyKey = _uuid.v4();
  }

  void _onFieldEdited() {
    if (_isProgrammaticChange) return;
    if (_isAiDraft &&
        _isApproved &&
        (_currentAnnouncement == null || _currentAnnouncement!.status == 'draft')) {
      setState(() {
        _isApproved = false;
      });
    }
  }

  void _onPushPreviewEdited() {
    _onFieldEdited();
    setState(() {}); // trigger rebuild for character counter
  }

  Future<void> _loadLocalData() async {
    await Future.wait([
      _fetchClassifications(),
      _fetchExistingAnnouncements(),
    ]);
  }

  Future<void> _fetchClassifications() async {
    setState(() => _isLoadingClassifications = true);
    try {
      final list = await widget.client.getClassifications(_selectedLocalId);
      if (!mounted) return;
      setState(() {
        _availableClassifications = list;
        _isLoadingClassifications = false;
      });
    } catch (_) {
      if (!mounted) return;
      setState(() => _isLoadingClassifications = false);
    }
  }

  Future<void> _fetchExistingAnnouncements() async {
    try {
      final list = await widget.client.getAnnouncements();
      if (!mounted) return;
      setState(() {
        _existingAnnouncements = list.map((item) {
          if (_currentAnnouncement != null && item.id == _currentAnnouncement!.id) {
            if (_currentAnnouncement!.status != 'draft' && item.status == 'draft') {
              return item.copyWith(
                status: _currentAnnouncement!.status,
                approvedAt: _currentAnnouncement!.approvedAt,
              );
            }
            if (_isApproved && !item.isApproved) {
              return item.copyWith(
                approvedAt: _currentAnnouncement!.approvedAt ?? DateTime.now().toIso8601String(),
              );
            }
          }
          return item;
        }).toList();
      });
    } catch (_) {
      // Survive error
    }
  }

  void selectAnnouncement(AnnouncementItem? item) => _selectAnnouncement(item);

  void _selectAnnouncement(AnnouncementItem? item) {
    _pollingTimer?.cancel();
    _pollingTimer = null;

    if (item == null) {
      _isProgrammaticChange = true;
      _titleController.clear();
      _bodyController.clear();
      _pushPreviewController.clear();
      _isProgrammaticChange = false;
      setState(() {
        _currentAnnouncement = null;
        _currentStats = null;
        _selectedClassifications.clear();
        _aiWarnings.clear();
        _aiError = null;
        _isAiDraft = false;
        _isApproved = false;
      });
      return;
    }

    _isProgrammaticChange = true;
    _titleController.text = item.title;
    _bodyController.text = item.body;
    _pushPreviewController.text = item.pushPreview;
    _isProgrammaticChange = false;

    setState(() {
      _currentAnnouncement = item;
      _currentStats = null;
      _selectedClassifications.clear();
      _selectedClassifications.addAll(item.audienceClassifications);
      _isAiDraft = item.source == 'ai';
      _isApproved = item.isApproved;
      _aiWarnings.clear();
      _aiError = null;
    });

    widget.client.getAnnouncement(item.id).then((detail) {
      if (mounted && _currentAnnouncement?.id == detail.id) {
        setState(() {
          _currentAnnouncement = detail;
          _isApproved = detail.isApproved;
        });
      }
    }).catchError((_) {});

    if (item.status != 'draft') {
      fetchStats();
      if (item.status != 'closed') {
        _startPollingStats();
      }
    }
  }

  Future<void> _handleDraftWithAI() async {
    final note = _rawNoteController.text.trim();
    if (note.isEmpty) return;

    setState(() {
      _isDraftingAI = true;
      _aiError = null;
      _aiWarnings.clear();
    });

    try {
      final result = await widget.client.draftWithAI(
        _selectedLocalId,
        note,
        idempotencyKey: _aiIdempotencyKey,
      );

      if (!mounted) return;

      _isProgrammaticChange = true;
      _titleController.text = result.title;
      _bodyController.text = result.body;
      _pushPreviewController.text = result.pushPreview;
      _isProgrammaticChange = false;

      setState(() {
        _aiWarnings = result.warnings;
        _isAiDraft = true;
        _isApproved = false;
        if (result.announcement != null) {
          _currentAnnouncement = result.announcement;
        }
        _isDraftingAI = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _aiError = e.toString();
        _isDraftingAI = false;
      });
    }
  }

  Future<void> _handleSaveDraft() async {
    final title = _titleController.text.trim();
    final body = _bodyController.text.trim();
    if (title.isEmpty || body.isEmpty) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('Title and body cannot be empty.')),
      );
      return;
    }

    setState(() {
      _isSavingDraft = true;
      _errorMessage = null;
      _actionMessage = null;
    });

    try {
      final saved = await widget.client.saveDraft(
        id: _currentAnnouncement?.id,
        title: title,
        body: body,
        pushPreview: _pushPreviewController.text.trim(),
        source: _isAiDraft ? 'ai' : 'manual',
        aiNote: _rawNoteController.text.isNotEmpty ? _rawNoteController.text : null,
        audienceClassifications: _selectedClassifications.toList(),
        idempotencyKey: _currentAnnouncement == null ? _uuid.v4() : null,
      );

      if (!mounted) return;

      setState(() {
        _currentAnnouncement = saved;
        if (_isAiDraft) {
          _isApproved = saved.isApproved;
        }
        _isSavingDraft = false;
        _actionMessage = 'Draft saved successfully.';
      });

      _fetchExistingAnnouncements();
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _errorMessage = e.toString();
        _isSavingDraft = false;
      });
    }
  }

  Future<void> _handleApprove() async {
    if (_currentAnnouncement == null) {
      await _handleSaveDraft();
    } else {
      // Save pending edits first
      await _handleSaveDraft();
    }

    if (_currentAnnouncement == null) return;
    if (!mounted) return;

    // Show confirmation dialog with exact title, body, push preview
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Confirm AI Draft Approval'),
        content: SingleChildScrollView(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            mainAxisSize: MainAxisSize.min,
            children: [
              const Text('Please review the announcement before approving:'),
              const SizedBox(height: 12),
              const Text('Title:', style: TextStyle(fontWeight: FontWeight.bold)),
              Text(_titleController.text),
              const SizedBox(height: 8),
              const Text('Body:', style: TextStyle(fontWeight: FontWeight.bold)),
              Text(_bodyController.text),
              const SizedBox(height: 8),
              const Text('Push Preview:', style: TextStyle(fontWeight: FontWeight.bold)),
              Text(_pushPreviewController.text.isNotEmpty ? _pushPreviewController.text : '(none)'),
            ],
          ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(ctx).pop(false),
            child: const Text('Cancel'),
          ),
          ElevatedButton(
            key: const Key('confirm_approve_button'),
            onPressed: () => Navigator.of(ctx).pop(true),
            child: const Text('Approve'),
          ),
        ],
      ),
    );

    if (confirmed != true || !mounted) return;

    setState(() {
      _isApproving = true;
      _errorMessage = null;
    });

    try {
      final approved = await widget.client.approve(_currentAnnouncement!.id);
      if (!mounted) return;
      setState(() {
        _currentAnnouncement = approved;
        _isApproved = true;
        _isApproving = false;
        _actionMessage = 'Announcement approved.';
        final idx = _existingAnnouncements.indexWhere((a) => a.id == approved.id);
        if (idx != -1) {
          _existingAnnouncements[idx] = approved;
        } else {
          _existingAnnouncements.insert(0, approved);
        }
      });
      _fetchExistingAnnouncements();
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _errorMessage = e.toString();
        _isApproving = false;
      });
    }
  }

  Future<void> _handleSend() async {
    if (_currentAnnouncement == null) return;

    final audienceDesc = _selectedClassifications.isEmpty
        ? 'All active members'
        : _selectedClassifications.join(', ');

    // Confirmation dialog naming local and audience
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Confirm Send Callout'),
        content: Text(
          'Send announcement to $_selectedLocalName?\n\nTarget Audience: $audienceDesc',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(ctx).pop(false),
            child: const Text('Cancel'),
          ),
          ElevatedButton(
            key: const Key('confirm_send_button'),
            onPressed: () => Navigator.of(ctx).pop(true),
            child: const Text('Confirm Send'),
          ),
        ],
      ),
    );

    if (confirmed != true || !mounted) return;

    setState(() {
      _isSending = true;
      _errorMessage = null;
      _actionMessage = null;
    });

    try {
      final sendKey = _uuid.v4();
      await widget.client.send(_currentAnnouncement!.id, idempotencyKey: sendKey);

      if (!mounted) return;

      final updated = _currentAnnouncement!.copyWith(
        status: 'sent',
        approvedAt: _currentAnnouncement!.approvedAt ?? DateTime.now().toIso8601String(),
      );

      setState(() {
        _currentAnnouncement = updated;
        _isApproved = true;
        _isSending = false;
        _actionMessage = 'Announcement sent! Polling delivery counts...';
        final idx = _existingAnnouncements.indexWhere((a) => a.id == updated.id);
        if (idx != -1) {
          _existingAnnouncements[idx] = updated;
        } else {
          _existingAnnouncements.insert(0, updated);
        }
      });

      await fetchStats();
      _startPollingStats();
      _fetchExistingAnnouncements();

      WidgetsBinding.instance.addPostFrameCallback((_) {
        if (_scrollController.hasClients) {
          _scrollController.animateTo(
            _scrollController.position.maxScrollExtent,
            duration: const Duration(milliseconds: 500),
            curve: Curves.easeOut,
          );
        }
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _errorMessage = e.toString();
        _isSending = false;
      });
    }
  }

  Future<void> fetchStats() async {
    final annId = _currentAnnouncement?.id;
    if (annId == null) return;

    if (_isPollingStats) return; // Prevent overlapping requests
    _isPollingStats = true;

    try {
      final stats = await widget.client.getStats(annId);
      if (!mounted) {
        _isPollingStats = false;
        return;
      }

      setState(() {
        _currentStats = stats;
        _isPollingStats = false;
        if (_currentAnnouncement != null && _currentAnnouncement!.status != stats.status) {
          final updated = _currentAnnouncement!.copyWith(status: stats.status);
          _currentAnnouncement = updated;
          final idx = _existingAnnouncements.indexWhere((a) => a.id == updated.id);
          if (idx != -1) {
            _existingAnnouncements[idx] = updated;
          }
        }
      });

      if (stats.status == 'closed') {
        _pollingTimer?.cancel();
        _pollingTimer = null;
      }
    } catch (_) {
      // Survive polling errors gracefully
      if (mounted) {
        setState(() => _isPollingStats = false);
      }
    }
  }

  void _startPollingStats() {
    _pollingTimer?.cancel();
    _pollingTimer = Timer.periodic(const Duration(seconds: 15), (_) {
      fetchStats();
    });
  }

  bool get _isSendEnabled {
    if (_isSending || _currentAnnouncement == null) return false;
    if (_currentAnnouncement!.status != 'draft') return false;

    if (_isAiDraft) {
      return _isApproved;
    } else {
      return true; // Manual saved draft
    }
  }

  void _handleLogout() {
    SessionStorage.instance.clearLeaderSession();
    widget.client.logout();
    Navigator.of(context).pushReplacement(
      MaterialPageRoute(
        builder: (_) => LoginScreen(client: widget.client),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final previewLength = _pushPreviewController.text.length;
    final isPreviewOverLimit = previewLength > 120;

    return Scaffold(
      appBar: AppBar(
        title: Text('CrewLink Leader ($_selectedLocalName)'),
        actions: [
          IconButton(
            key: const Key('logout_button'),
            icon: const Icon(Icons.logout),
            tooltip: 'Log Out',
            onPressed: _handleLogout,
          ),
        ],
      ),
      body: Column(
        children: [
          if (_currentAnnouncement != null &&
              (_currentAnnouncement!.status != 'draft' || _currentStats != null))
            _buildPinnedAnnouncementBanner(),
          Expanded(
            child: SingleChildScrollView(
              key: const Key('leader_screen_scroll_view'),
              controller: _scrollController,
              padding: const EdgeInsets.all(16.0),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  // Status/Error messages
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
                  if (_actionMessage != null) ...[
                    Container(
                      padding: const EdgeInsets.all(12),
                      margin: const EdgeInsets.only(bottom: 12),
                      decoration: BoxDecoration(
                        color: Colors.green.shade100,
                        borderRadius: BorderRadius.circular(4),
                        border: Border.all(color: Colors.green.shade400),
                      ),
                      child: Text(
                        _actionMessage!,
                        style: TextStyle(color: Colors.green.shade900),
                      ),
                    ),
                  ],

                  // Local Picker & Existing Announcements
                  Card(
                    child: Padding(
                      padding: const EdgeInsets.all(12.0),
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          const Text('Local & Existing Drafts', style: TextStyle(fontWeight: FontWeight.bold)),
                          const SizedBox(height: 8),
                          Row(
                            children: [
                              Expanded(
                                child: InputDecorator(
                                  decoration: const InputDecoration(labelText: 'Local', border: OutlineInputBorder()),
                                  child: DropdownButtonHideUnderline(
                                    child: DropdownButton<String>(
                                      key: const Key('local_picker'),
                                      value: _selectedLocalId,
                                      isExpanded: true,
                                      isDense: true,
                                      items: () {
                                        final options = <String, String>{};
                                        for (final role in widget.userProfile.leadershipRoles) {
                                          final id = role.localId.isNotEmpty ? role.localId : widget.userProfile.localId;
                                          final name = role.localName.isNotEmpty
                                              ? role.localName
                                              : (widget.userProfile.localName.isNotEmpty
                                                  ? widget.userProfile.localName
                                                  : id);
                                          if (id.isNotEmpty) {
                                            options[id] = name;
                                          }
                                        }
                                        if (options.isEmpty && _selectedLocalId.isNotEmpty) {
                                          options[_selectedLocalId] = _selectedLocalName.isNotEmpty
                                              ? _selectedLocalName
                                              : _selectedLocalId;
                                        } else if (_selectedLocalId.isNotEmpty && !options.containsKey(_selectedLocalId)) {
                                          options[_selectedLocalId] = _selectedLocalName.isNotEmpty
                                              ? _selectedLocalName
                                              : _selectedLocalId;
                                        }
                                        return options.entries.map((e) {
                                          return DropdownMenuItem<String>(
                                            value: e.key,
                                            child: Text(e.value, overflow: TextOverflow.ellipsis),
                                          );
                                        }).toList();
                                      }(),
                                      onChanged: (val) {
                                        if (val != null && val != _selectedLocalId) {
                                          String newName = val;
                                          for (final role in widget.userProfile.leadershipRoles) {
                                            if (role.localId == val && role.localName.isNotEmpty) {
                                              newName = role.localName;
                                              break;
                                            }
                                          }
                                          if (newName == val &&
                                              val == widget.userProfile.localId &&
                                              widget.userProfile.localName.isNotEmpty) {
                                            newName = widget.userProfile.localName;
                                          }
                                          setState(() {
                                            _selectedLocalId = val;
                                            _selectedLocalName = newName;
                                            _selectedClassifications.clear();
                                            _currentAnnouncement = null;
                                            _currentStats = null;
                                          });
                                          _loadLocalData();
                                        }
                                      },
                                    ),
                                  ),
                                ),
                              ),
                        const SizedBox(width: 12),
                        Expanded(
                          child: Builder(
                            builder: (context) {
                              final displayAnnouncements = _existingAnnouncements.map((a) {
                                if (_currentAnnouncement != null && a.id == _currentAnnouncement!.id) {
                                  return _currentAnnouncement!;
                                }
                                return a;
                              }).toList();
                              if (_currentAnnouncement != null &&
                                  !displayAnnouncements.any((a) => a.id == _currentAnnouncement!.id)) {
                                displayAnnouncements.insert(0, _currentAnnouncement!);
                              }
                              return InputDecorator(
                                decoration: const InputDecoration(labelText: 'Open Existing', border: OutlineInputBorder()),
                                child: DropdownButtonHideUnderline(
                                  child: DropdownButton<String?>(
                                    key: const Key('existing_announcements_dropdown'),
                                    value: _currentAnnouncement?.id,
                                    isExpanded: true,
                                    isDense: true,
                                    items: [
                                      const DropdownMenuItem<String?>(
                                        value: null,
                                        child: Text('-- New Announcement --'),
                                      ),
                                      ...displayAnnouncements.map((a) {
                                        return DropdownMenuItem<String?>(
                                          value: a.id,
                                          child: Text('${a.title} (${a.status})', overflow: TextOverflow.ellipsis),
                                        );
                                      }),
                                    ],
                                    onChanged: (val) {
                                      if (val == null) {
                                        _selectAnnouncement(null);
                                      } else {
                                        final item = displayAnnouncements.firstWhere((a) => a.id == val);
                                        _selectAnnouncement(item);
                                      }
                                    },
                                  ),
                                ),
                              );
                            },
                          ),
                        ),
                      ],
                    ),
                  ],
                ),
              ),
            ),
            const SizedBox(height: 12),

            // Audience Classification Chips
            Card(
              child: Padding(
                padding: const EdgeInsets.all(12.0),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    const Text('Audience (Optional)', style: TextStyle(fontWeight: FontWeight.bold)),
                    const SizedBox(height: 8),
                    if (_isLoadingClassifications)
                      const LinearProgressIndicator()
                    else if (_availableClassifications.isEmpty)
                      const Text('All active members will be targeted.')
                    else
                      Wrap(
                        spacing: 8,
                        children: _availableClassifications.map((c) {
                          final isSelected = _selectedClassifications.contains(c);
                          return FilterChip(
                            key: Key('audience_chip_$c'),
                            label: Text(c),
                            selected: isSelected,
                            onSelected: (selected) {
                              setState(() {
                                if (selected) {
                                  _selectedClassifications.add(c);
                                } else {
                                  _selectedClassifications.remove(c);
                                }
                              });
                            },
                          );
                        }).toList(),
                      ),
                  ],
                ),
              ),
            ),
            const SizedBox(height: 12),

            // AI Drafting Section
            Card(
              child: Padding(
                padding: const EdgeInsets.all(12.0),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    const Text('Draft with AI', style: TextStyle(fontWeight: FontWeight.bold)),
                    const SizedBox(height: 8),
                    TextField(
                      key: const Key('ai_note_field'),
                      controller: _rawNoteController,
                      maxLines: 3,
                      decoration: const InputDecoration(
                        hintText: 'Paste messy notes, dispatch text, or callout instructions...',
                        border: OutlineInputBorder(),
                      ),
                    ),
                    const SizedBox(height: 8),
                    if (_aiError != null) ...[
                      Container(
                        padding: const EdgeInsets.all(8),
                        margin: const EdgeInsets.only(bottom: 8),
                        decoration: BoxDecoration(
                          color: Colors.amber.shade100,
                          borderRadius: BorderRadius.circular(4),
                          border: Border.all(color: Colors.amber.shade400),
                        ),
                        child: Text(
                          _aiError!,
                          style: TextStyle(color: Colors.amber.shade900),
                        ),
                      ),
                    ],
                    Row(
                      children: [
                        ElevatedButton.icon(
                          key: const Key('draft_with_ai_button'),
                          onPressed: _isDraftingAI ? null : _handleDraftWithAI,
                          icon: _isDraftingAI
                              ? const SizedBox(
                                  width: 16,
                                  height: 16,
                                  child: CircularProgressIndicator(strokeWidth: 2),
                                )
                              : const Icon(Icons.auto_awesome),
                          label: const Text('Draft with AI'),
                        ),
                      ],
                    ),
                    if (_aiWarnings.isNotEmpty) ...[
                      const SizedBox(height: 8),
                      const Text('AI Fact-Check Warnings:', style: TextStyle(fontWeight: FontWeight.bold, color: Colors.orange)),
                      ..._aiWarnings.map((w) => Text('• $w', style: const TextStyle(color: Colors.orange))),
                    ],
                  ],
                ),
              ),
            ),
            const SizedBox(height: 12),

            // Announcement Form (Manual Path & AI Result)
            Card(
              child: Padding(
                padding: const EdgeInsets.all(12.0),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Row(
                      mainAxisAlignment: MainAxisAlignment.spaceBetween,
                      children: [
                        const Expanded(
                          child: Text('Announcement Content', style: TextStyle(fontWeight: FontWeight.bold)),
                        ),
                        if (_isAiDraft)
                          Chip(
                            key: const Key('approval_status_chip'),
                            label: Text(_isApproved ? 'Approved' : 'Not approved'),
                            backgroundColor: _isApproved ? Colors.green.shade100 : Colors.orange.shade100,
                            labelStyle: TextStyle(
                              color: _isApproved ? Colors.green.shade900 : Colors.orange.shade900,
                              fontWeight: FontWeight.bold,
                            ),
                          ),
                      ],
                    ),
                    const SizedBox(height: 8),
                    TextField(
                      key: const Key('title_field'),
                      controller: _titleController,
                      decoration: const InputDecoration(
                        labelText: 'Title',
                        border: OutlineInputBorder(),
                      ),
                    ),
                    const SizedBox(height: 12),
                    TextField(
                      key: const Key('body_field'),
                      controller: _bodyController,
                      maxLines: 4,
                      decoration: const InputDecoration(
                        labelText: 'Body',
                        border: OutlineInputBorder(),
                      ),
                    ),
                    const SizedBox(height: 12),
                    TextField(
                      key: const Key('push_preview_field'),
                      controller: _pushPreviewController,
                      decoration: InputDecoration(
                        labelText: 'Push Notification Preview (Optional)',
                        border: const OutlineInputBorder(),
                        counterText: '$previewLength/120',
                        counterStyle: TextStyle(
                          color: isPreviewOverLimit ? Colors.red : Colors.grey.shade700,
                          fontWeight: isPreviewOverLimit ? FontWeight.bold : FontWeight.normal,
                        ),
                      ),
                    ),
                    const SizedBox(height: 16),
                    Wrap(
                      spacing: 8,
                      runSpacing: 8,
                      children: [
                        ElevatedButton(
                          key: const Key('save_draft_button'),
                          onPressed: _isSavingDraft ? null : _handleSaveDraft,
                          child: _isSavingDraft
                              ? const SizedBox(
                                  width: 16,
                                  height: 16,
                                  child: CircularProgressIndicator(strokeWidth: 2),
                                )
                              : const Text('Save draft'),
                        ),
                        if (_isAiDraft)
                          ElevatedButton(
                            key: const Key('approve_button'),
                            onPressed: _isApproving ? null : _handleApprove,
                            style: ElevatedButton.styleFrom(
                              backgroundColor: Colors.teal.shade50,
                            ),
                            child: _isApproving
                                ? const SizedBox(
                                    width: 16,
                                    height: 16,
                                    child: CircularProgressIndicator(strokeWidth: 2),
                                  )
                                : const Text('Approve'),
                          ),
                        ElevatedButton(
                          key: const Key('send_button'),
                          onPressed: _isSendEnabled ? _handleSend : null,
                          style: ElevatedButton.styleFrom(
                            backgroundColor: Colors.blue.shade600,
                            foregroundColor: Colors.white,
                          ),
                          child: _isSending
                              ? const SizedBox(
                                  width: 16,
                                  height: 16,
                                  child: CircularProgressIndicator(
                                    strokeWidth: 2,
                                    color: Colors.white,
                                  ),
                                )
                              : const Text('Send'),
                        ),
                      ],
                    ),
                  ],
                ),
              ),
            ),
            const SizedBox(height: 12),

            // Stats / Counts Table
            if (_currentStats != null) ...[
              Card(
                key: const Key('stats_card'),
                child: Padding(
                  padding: const EdgeInsets.all(12.0),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Row(
                        mainAxisAlignment: MainAxisAlignment.spaceBetween,
                        children: [
                          Expanded(
                            child: Text(
                              'Delivery Counts (${_currentStats!.status.toUpperCase()})',
                              style: const TextStyle(fontWeight: FontWeight.bold),
                            ),
                          ),
                          IconButton(
                            key: const Key('refresh_stats_button'),
                            icon: const Icon(Icons.refresh),
                            tooltip: 'Refresh Counts',
                            onPressed: fetchStats,
                          ),
                        ],
                      ),
                      if (_currentStats!.asOf.isNotEmpty)
                        Text(
                          'As of: ${_currentStats!.asOf}',
                          style: TextStyle(fontSize: 12, color: Colors.grey.shade600),
                        ),
                      const SizedBox(height: 8),
                      Table(
                        border: TableBorder.all(color: Colors.grey.shade300),
                        children: [
                          _buildTableRow('Targeted', '${_currentStats!.targeted}'),
                          _buildTableRow('Sent', '${_currentStats!.sent}'),
                          _buildTableRow('Push Failed', '${_currentStats!.pushFailed}'),
                          _buildTableRow('Delivered', '${_currentStats!.delivered}'),
                          _buildTableRow('Read', '${_currentStats!.read}'),
                          _buildTableRow('Acknowledged', '${_currentStats!.acknowledged}'),
                          _buildTableRow('Coming', '${_currentStats!.coming}'),
                          _buildTableRow("Can't Make It", '${_currentStats!.cantMakeIt}'),
                          _buildTableRow('No Response', '${_currentStats!.noResponse}'),
                        ],
                      ),
                    ],
                  ),
                ),
              ),
            ],
          ],
        ),
      ),
    ),
  ],
),
);
  }

  Widget _buildPinnedAnnouncementBanner() {
    final title = _titleController.text.isNotEmpty
        ? _titleController.text
        : (_currentAnnouncement?.title ?? '');
    final body = _bodyController.text.isNotEmpty
        ? _bodyController.text
        : (_currentAnnouncement?.body ?? '');
    final status = _currentAnnouncement?.status ?? 'sent';
    final preview = _pushPreviewController.text.isNotEmpty
        ? _pushPreviewController.text
        : (_currentAnnouncement?.pushPreview ?? '');

    return Container(
      key: const Key('pinned_announcement_header'),
      width: double.infinity,
      padding: const EdgeInsets.symmetric(horizontal: 16.0, vertical: 10.0),
      decoration: BoxDecoration(
        color: Colors.blue.shade50,
        border: Border(bottom: BorderSide(color: Colors.blue.shade200, width: 1.5)),
        boxShadow: const [
          BoxShadow(
            color: Color(0x0F000000),
            offset: Offset(0, 2),
            blurRadius: 4,
          ),
        ],
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisSize: MainAxisSize.min,
        children: [
          Row(
            children: [
              const Icon(Icons.campaign, color: Colors.blue, size: 22),
              const SizedBox(width: 8),
              Expanded(
                child: Text(
                  title.isNotEmpty ? title : 'Announcement',
                  style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 16),
                  overflow: TextOverflow.ellipsis,
                ),
              ),
              const SizedBox(width: 8),
              Chip(
                label: Text(status.toUpperCase()),
                backgroundColor: status == 'sent' ? Colors.green.shade100 : Colors.blue.shade100,
                labelStyle: TextStyle(
                  color: status == 'sent' ? Colors.green.shade900 : Colors.blue.shade900,
                  fontWeight: FontWeight.bold,
                  fontSize: 11,
                ),
                visualDensity: VisualDensity.compact,
                padding: EdgeInsets.zero,
              ),
            ],
          ),
          const SizedBox(height: 6),
          ConstrainedBox(
            constraints: const BoxConstraints(maxHeight: 100),
            child: SingleChildScrollView(
              child: Text(
                body,
                style: const TextStyle(fontSize: 14),
              ),
            ),
          ),
          if (preview.isNotEmpty) ...[
            const SizedBox(height: 4),
            Text(
              'Push preview: $preview',
              style: TextStyle(fontSize: 12, fontStyle: FontStyle.italic, color: Colors.grey.shade700),
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
            ),
          ],
        ],
      ),
    );
  }

  TableRow _buildTableRow(String label, String value) {
    return TableRow(
      children: [
        Padding(
          padding: const EdgeInsets.all(8.0),
          child: Text(label, style: const TextStyle(fontWeight: FontWeight.w500)),
        ),
        Padding(
          padding: const EdgeInsets.all(8.0),
          child: Text(value, textAlign: TextAlign.right, key: Key('stat_$label')),
        ),
      ],
    );
  }
}
