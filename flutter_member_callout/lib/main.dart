import 'dart:ui';
import 'package:flutter/material.dart';

import 'api/api_client.dart';
import 'screens/leader_screen.dart';
import 'screens/login_screen.dart';
import 'screens/member_screen.dart';
import 'storage/session_storage.dart';

void main() {
  runApp(const CrewLinkApp());
}

class CrewLinkApp extends StatefulWidget {
  final ApiClient? client;

  const CrewLinkApp({super.key, this.client});

  @override
  State<CrewLinkApp> createState() => _CrewLinkAppState();
}

class _CrewLinkAppState extends State<CrewLinkApp> {
  late final ApiClient _client;
  final _navKey = GlobalKey<NavigatorState>();
  bool _isRestoring = true;
  Widget? _restoredScreen;

  @override
  void initState() {
    super.initState();
    final savedBaseUrl =
        SessionStorage.instance.getServerUrl() ?? 'http://localhost:8000';
    _client = widget.client ?? ApiClient(baseUrl: savedBaseUrl);

    _client.onUnauthorized = () {
      SessionStorage.instance.clearLeaderSession();
      SessionStorage.instance.clearMemberSession();
      _navKey.currentState?.pushAndRemoveUntil(
        MaterialPageRoute(builder: (_) => LoginScreen(client: _client)),
        (route) => false,
      );
    };

    _attemptSessionRestore();
  }

  Future<void> _attemptSessionRestore() async {
    // 1. Determine target flow
    final hash = SessionStorage.instance.getUrlHash();
    String? targetFlow;
    if (hash.contains('leader')) {
      targetFlow = 'leader';
    } else if (hash.contains('member')) {
      targetFlow = 'member';
    } else {
      targetFlow = SessionStorage.instance.getActiveFlow();
    }

    String? tokenToRestore;
    if (targetFlow == 'leader') {
      tokenToRestore = SessionStorage.instance.getLeaderToken();
    } else if (targetFlow == 'member') {
      tokenToRestore = SessionStorage.instance.getMemberToken();
    }

    if (tokenToRestore != null && tokenToRestore.isNotEmpty) {
      _client.setToken(tokenToRestore);
      try {
        final me = await _client.getMe();
        if (!mounted) return;

        if (targetFlow == 'leader' && me.isLeader) {
          SessionStorage.instance.setUrlHash('/leader');
          setState(() {
            _restoredScreen = LeaderScreen(client: _client, userProfile: me);
            _isRestoring = false;
          });
          return;
        } else if (targetFlow == 'member') {
          SessionStorage.instance.setUrlHash('/member');
          setState(() {
            _restoredScreen = MemberScreen(client: _client, userProfile: me);
            _isRestoring = false;
          });
          return;
        }
      } catch (_) {
        // Token invalid or expired
        if (targetFlow == 'leader') {
          SessionStorage.instance.clearLeaderSession();
        } else {
          SessionStorage.instance.clearMemberSession();
        }
        _client.setToken(null);
      }
    }

    if (mounted) {
      setState(() {
        _restoredScreen = LoginScreen(client: _client);
        _isRestoring = false;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      debugShowCheckedModeBanner: false,
      navigatorKey: _navKey,
      title: 'CrewLink',
      scrollBehavior: const MaterialScrollBehavior().copyWith(
        dragDevices: {
          PointerDeviceKind.mouse,
          PointerDeviceKind.touch,
          PointerDeviceKind.trackpad,
          PointerDeviceKind.stylus,
        },
      ),
      theme: ThemeData(useMaterial3: true, primarySwatch: Colors.blue),
      home: _isRestoring
          ? const Scaffold(body: Center(child: CircularProgressIndicator()))
          : (_restoredScreen ?? LoginScreen(client: _client)),
    );
  }
}
