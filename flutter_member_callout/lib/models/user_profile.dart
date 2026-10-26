class LeadershipRole {
  final int id;
  final String localId;
  final String localName;
  final String role;

  LeadershipRole({
    required this.id,
    required this.localId,
    required this.localName,
    required this.role,
  });

  factory LeadershipRole.fromJson(
    Map<String, dynamic> json, {
    String fallbackLocalId = '',
    String fallbackLocalName = '',
  }) {
    final localMap = json['local'] is Map<String, dynamic>
        ? json['local'] as Map<String, dynamic>
        : null;
    final lId = json['local_id'] as String? ?? localMap?['id'] as String? ?? fallbackLocalId;
    final lName = json['local_name'] as String? ?? localMap?['name'] as String? ?? fallbackLocalName;
    return LeadershipRole(
      id: json['id'] as int? ?? 0,
      localId: lId.isNotEmpty ? lId : fallbackLocalId,
      localName: lName.isNotEmpty ? lName : (fallbackLocalName.isNotEmpty ? fallbackLocalName : lId),
      role: json['role'] as String? ?? 'leader',
    );
  }
}

class UserProfile {
  final String id;
  final String fullName;
  final String email;
  final String classification;
  final String localId;
  final String localName;
  final List<LeadershipRole> leadershipRoles;

  UserProfile({
    required this.id,
    required this.fullName,
    required this.email,
    required this.classification,
    required this.localId,
    required this.localName,
    required this.leadershipRoles,
  });

  bool get isLeader => leadershipRoles.isNotEmpty;

  factory UserProfile.fromJson(Map<String, dynamic> json) {
    final localMap = json['local'] is Map<String, dynamic>
        ? json['local'] as Map<String, dynamic>
        : null;
    final parsedLocalId = localMap?['id'] as String? ?? json['local_id'] as String? ?? '';
    final parsedLocalName = localMap?['name'] as String? ?? json['local_name'] as String? ?? '';

    final rolesRaw = json['leadership_roles'] as List<dynamic>? ?? [];
    return UserProfile(
      id: json['id'] as String? ?? '',
      fullName: json['full_name'] as String? ?? '',
      email: json['email'] as String? ?? '',
      classification: json['classification'] as String? ?? '',
      localId: parsedLocalId,
      localName: parsedLocalName,
      leadershipRoles: rolesRaw
          .map((r) => LeadershipRole.fromJson(
                r as Map<String, dynamic>,
                fallbackLocalId: parsedLocalId,
                fallbackLocalName: parsedLocalName,
              ))
          .toList(),
    );
  }
}
