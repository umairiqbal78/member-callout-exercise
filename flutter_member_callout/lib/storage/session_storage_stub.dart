import 'session_storage.dart';

SessionStorage createSessionStorage() => InMemorySessionStorage();

class InMemorySessionStorage implements SessionStorage {
  final Map<String, String> _data = {};
  String _hash = '';

  @override
  String? getLeaderToken() => _data['crewlink_leader_token'];

  @override
  void setLeaderToken(String? token) {
    if (token == null) {
      _data.remove('crewlink_leader_token');
    } else {
      _data['crewlink_leader_token'] = token;
    }
  }

  @override
  String? getMemberToken() => _data['crewlink_member_token'];

  @override
  void setMemberToken(String? token) {
    if (token == null) {
      _data.remove('crewlink_member_token');
    } else {
      _data['crewlink_member_token'] = token;
    }
  }

  @override
  String? getActiveFlow() => _data['crewlink_active_flow'];

  @override
  void setActiveFlow(String? flow) {
    if (flow == null) {
      _data.remove('crewlink_active_flow');
    } else {
      _data['crewlink_active_flow'] = flow;
    }
  }

  @override
  String? getServerUrl() => _data['crewlink_server_url'];

  @override
  void setServerUrl(String? url) {
    if (url == null) {
      _data.remove('crewlink_server_url');
    } else {
      _data['crewlink_server_url'] = url;
    }
  }

  @override
  String getUrlHash() => _hash;

  @override
  void setUrlHash(String hash) {
    _hash = hash;
  }

  @override
  void clearLeaderSession() {
    _data.remove('crewlink_leader_token');
    if (_data['crewlink_active_flow'] == 'leader') {
      _data.remove('crewlink_active_flow');
    }
    _hash = '';
  }

  @override
  void clearMemberSession() {
    _data.remove('crewlink_member_token');
    if (_data['crewlink_active_flow'] == 'member') {
      _data.remove('crewlink_active_flow');
    }
    _hash = '';
  }
}
