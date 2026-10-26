import 'session_storage_stub.dart'
    if (dart.library.html) 'session_storage_web.dart';

abstract class SessionStorage {
  static SessionStorage instance = createSessionStorage();

  String? getLeaderToken();
  void setLeaderToken(String? token);

  String? getMemberToken();
  void setMemberToken(String? token);

  String? getActiveFlow();
  void setActiveFlow(String? flow);

  String? getServerUrl();
  void setServerUrl(String? url);

  String getUrlHash();
  void setUrlHash(String hash);

  void clearLeaderSession();
  void clearMemberSession();
}
