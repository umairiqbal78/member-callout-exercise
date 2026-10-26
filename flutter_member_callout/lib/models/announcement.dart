class AnnouncementItem {
  final String id;
  final String localId;
  final String title;
  final String body;
  final bool needsAck;
  final String? startsAt;
  final String status;
  final String source;
  final List<String> audienceClassifications;
  final String pushPreview;
  final String? approvedAt;
  final String? aiNote;
  final String? aiModel;
  final String createdAt;

  AnnouncementItem({
    required this.id,
    required this.localId,
    required this.title,
    required this.body,
    required this.needsAck,
    this.startsAt,
    required this.status,
    required this.source,
    required this.audienceClassifications,
    required this.pushPreview,
    this.approvedAt,
    this.aiNote,
    this.aiModel,
    required this.createdAt,
  });

  bool get isApproved =>
      (approvedAt != null && approvedAt!.isNotEmpty) || status != 'draft';
  bool get isAi => source == 'ai';
  bool get isDraft => status == 'draft';
  bool get isClosed => status == 'closed';

  AnnouncementItem copyWith({
    String? id,
    String? localId,
    String? title,
    String? body,
    bool? needsAck,
    String? startsAt,
    String? status,
    String? source,
    List<String>? audienceClassifications,
    String? pushPreview,
    String? approvedAt,
    String? aiNote,
    String? aiModel,
    String? createdAt,
  }) {
    return AnnouncementItem(
      id: id ?? this.id,
      localId: localId ?? this.localId,
      title: title ?? this.title,
      body: body ?? this.body,
      needsAck: needsAck ?? this.needsAck,
      startsAt: startsAt ?? this.startsAt,
      status: status ?? this.status,
      source: source ?? this.source,
      audienceClassifications:
          audienceClassifications ?? this.audienceClassifications,
      pushPreview: pushPreview ?? this.pushPreview,
      approvedAt: approvedAt ?? this.approvedAt,
      aiNote: aiNote ?? this.aiNote,
      aiModel: aiModel ?? this.aiModel,
      createdAt: createdAt ?? this.createdAt,
    );
  }

  factory AnnouncementItem.fromJson(Map<String, dynamic> json) {
    final audienceRaw = json['audience_classifications'] as List<dynamic>? ?? [];
    return AnnouncementItem(
      id: json['id'] as String? ?? '',
      localId: json['local_id'] as String? ?? '',
      title: json['title'] as String? ?? '',
      body: json['body'] as String? ?? '',
      needsAck: json['needs_ack'] as bool? ?? false,
      startsAt: json['starts_at'] as String?,
      status: json['status'] as String? ?? 'draft',
      source: json['source'] as String? ?? 'manual',
      audienceClassifications: audienceRaw.map((e) => e.toString()).toList(),
      pushPreview: json['push_preview'] as String? ?? '',
      approvedAt: json['approved_at'] as String?,
      aiNote: json['ai_note'] as String?,
      aiModel: json['ai_model'] as String?,
      createdAt: json['created_at'] as String? ?? '',
    );
  }
}

class AnnouncementStats {
  final int targeted;
  final int sent;
  final int pushFailed;
  final int delivered;
  final int read;
  final int acknowledged;
  final int coming;
  final int cantMakeIt;
  final int noResponse;
  final String status;
  final String asOf;

  AnnouncementStats({
    required this.targeted,
    required this.sent,
    required this.pushFailed,
    required this.delivered,
    required this.read,
    required this.acknowledged,
    required this.coming,
    required this.cantMakeIt,
    required this.noResponse,
    required this.status,
    required this.asOf,
  });

  factory AnnouncementStats.fromJson(Map<String, dynamic> json) {
    return AnnouncementStats(
      targeted: (json['targeted'] as num?)?.toInt() ?? 0,
      sent: (json['sent'] as num?)?.toInt() ?? 0,
      pushFailed: (json['push_failed'] as num?)?.toInt() ?? 0,
      delivered: (json['delivered'] as num?)?.toInt() ?? 0,
      read: (json['read'] as num?)?.toInt() ?? 0,
      acknowledged: (json['acknowledged'] as num?)?.toInt() ?? 0,
      coming: (json['coming'] as num?)?.toInt() ?? 0,
      cantMakeIt: (json['cant_make_it'] as num?)?.toInt() ?? 0,
      noResponse: (json['no_response'] as num?)?.toInt() ?? 0,
      status: json['status'] as String? ?? '',
      asOf: json['as_of'] as String? ?? '',
    );
  }
}

class AIDraftResult {
  final AnnouncementItem? announcement;
  final String title;
  final String body;
  final String pushPreview;
  final List<String> warnings;

  AIDraftResult({
    this.announcement,
    required this.title,
    required this.body,
    required this.pushPreview,
    required this.warnings,
  });

  factory AIDraftResult.fromJson(Map<String, dynamic> json) {
    final warningsRaw = json['warnings'] as List<dynamic>? ?? [];
    AnnouncementItem? item;
    String t = '';
    String b = '';
    String p = '';

    if (json.containsKey('announcement') && json['announcement'] is Map<String, dynamic>) {
      item = AnnouncementItem.fromJson(json['announcement'] as Map<String, dynamic>);
      t = item.title;
      b = item.body;
      p = item.pushPreview;
    } else {
      t = json['title'] as String? ?? '';
      b = json['body'] as String? ?? '';
      p = json['push_preview'] as String? ?? '';
    }

    return AIDraftResult(
      announcement: item,
      title: t,
      body: b,
      pushPreview: p,
      warnings: warningsRaw.map((e) => e.toString()).toList(),
    );
  }
}
