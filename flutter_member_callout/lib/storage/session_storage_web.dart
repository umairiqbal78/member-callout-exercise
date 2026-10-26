// ignore_for_file: deprecated_member_use, avoid_web_libraries_in_flutter
import 'dart:html' as html;

import 'session_storage.dart';

SessionStorage createSessionStorage() => WebSessionStorage();

class WebSessionStorage implements SessionStorage {
  @override
  String? getLeaderToken() => html.window.localStorage['crewlink_leader_token'];

  @override
  void setLeaderToken(String? token) {
    if (token == null) {
      html.window.localStorage.remove('crewlink_leader_token');
    } else {
      html.window.localStorage['crewlink_leader_token'] = token;
    }
  }

  @override
  String? getMemberToken() => html.window.localStorage['crewlink_member_token'];

  @override
  void setMemberToken(String? token) {
    if (token == null) {
      html.window.localStorage.remove('crewlink_member_token');
    } else {
      html.window.localStorage['crewlink_member_token'] = token;
    }
  }

  @override
  String? getActiveFlow() => html.window.localStorage['crewlink_active_flow'];

  @override
  void setActiveFlow(String? flow) {
    if (flow == null) {
      html.window.localStorage.remove('crewlink_active_flow');
    } else {
      html.window.localStorage['crewlink_active_flow'] = flow;
    }
  }

  @override
  String? getServerUrl() => html.window.localStorage['crewlink_server_url'];

  @override
  void setServerUrl(String? url) {
    if (url == null) {
      html.window.localStorage.remove('crewlink_server_url');
    } else {
      html.window.localStorage['crewlink_server_url'] = url;
    }
  }

  @override
  String getUrlHash() {
    final hash = html.window.location.hash;
    return hash.startsWith('#') ? hash.substring(1) : hash;
  }

  @override
  void setUrlHash(String hash) {
    final formatted = hash.startsWith('#') ? hash : '#$hash';
    html.window.location.hash = formatted;
  }

  @override
  void clearLeaderSession() {
    html.window.localStorage.remove('crewlink_leader_token');
    if (html.window.localStorage['crewlink_active_flow'] == 'leader') {
      html.window.localStorage.remove('crewlink_active_flow');
    }
    if (getUrlHash().contains('leader')) {
      html.window.location.hash = '';
    }
  }

  @override
  void clearMemberSession() {
    html.window.localStorage.remove('crewlink_member_token');
    if (html.window.localStorage['crewlink_active_flow'] == 'member') {
      html.window.localStorage.remove('crewlink_active_flow');
    }
    if (getUrlHash().contains('member')) {
      html.window.location.hash = '';
    }
  }
}
