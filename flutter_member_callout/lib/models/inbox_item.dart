class InboxItem {
  final int id;
  final String announcementId;
  final String announcementTitle;
  final String announcementBody;
  final String? announcementStartsAt;
  final bool announcementNeedsAck;
  final String? deliveredAt;
  final String? readAt;
  final String? acknowledgedAt;
  String? response;
  final String? respondedAt;

  InboxItem({
    required this.id,
    required this.announcementId,
    required this.announcementTitle,
    required this.announcementBody,
    this.announcementStartsAt,
    required this.announcementNeedsAck,
    this.deliveredAt,
    this.readAt,
    this.acknowledgedAt,
    this.response,
    this.respondedAt,
  });

  bool get isDelivered => deliveredAt != null && deliveredAt!.isNotEmpty;
  bool get isRead => readAt != null && readAt!.isNotEmpty;
  bool get isAcknowledged => acknowledgedAt != null && acknowledgedAt!.isNotEmpty;
  bool get hasMeetingTime => announcementStartsAt != null && announcementStartsAt!.isNotEmpty;

  factory InboxItem.fromJson(Map<String, dynamic> json) {
    return InboxItem(
      id: (json['id'] as num?)?.toInt() ?? 0,
      announcementId: json['announcement_id'] as String? ?? '',
      announcementTitle: json['announcement_title'] as String? ?? '',
      announcementBody: json['announcement_body'] as String? ?? '',
      announcementStartsAt: json['announcement_starts_at'] as String?,
      announcementNeedsAck: json['announcement_needs_ack'] as bool? ?? false,
      deliveredAt: json['delivered_at'] as String?,
      readAt: json['read_at'] as String?,
      acknowledgedAt: json['acknowledged_at'] as String?,
      response: json['response'] as String?,
      respondedAt: json['responded_at'] as String?,
    );
  }
}

class PaginatedInbox {
  final List<InboxItem> items;
  final String? nextCursor;

  PaginatedInbox({
    required this.items,
    this.nextCursor,
  });
}
