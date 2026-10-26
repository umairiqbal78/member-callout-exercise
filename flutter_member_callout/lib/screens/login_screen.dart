import 'package:flutter/material.dart';

import '../api/api_client.dart';
import '../storage/session_storage.dart';
import 'leader_screen.dart';
import 'member_screen.dart';

class LoginScreen extends StatefulWidget {
  final ApiClient client;

  const LoginScreen({super.key, required this.client});

  @override
  State<LoginScreen> createState() => _LoginScreenState();
}

class _LoginScreenState extends State<LoginScreen> {
  final _formKey = GlobalKey<FormState>();
  late final TextEditingController _serverUrlController;
  final _usernameController = TextEditingController();
  final _passwordController = TextEditingController();

  bool _isLoading = false;
  String? _errorMessage;

  @override
  void initState() {
    super.initState();
    final savedUrl = SessionStorage.instance.getServerUrl();
    if (savedUrl != null && savedUrl.isNotEmpty) {
      widget.client.baseUrl = savedUrl;
    }
    _serverUrlController = TextEditingController(text: widget.client.baseUrl);
  }

  @override
  void dispose() {
    _serverUrlController.dispose();
    _usernameController.dispose();
    _passwordController.dispose();
    super.dispose();
  }

  Future<void> _handleLogin() async {
    if (!_formKey.currentState!.validate()) return;

    setState(() {
      _isLoading = true;
      _errorMessage = null;
    });

    try {
      widget.client.baseUrl = _serverUrlController.text.trim();
      final token = await widget.client.login(
        _usernameController.text,
        _passwordController.text,
      );

      final me = await widget.client.getMe();

      if (!mounted) return;

      SessionStorage.instance.setServerUrl(widget.client.baseUrl);

      if (me.isLeader) {
        SessionStorage.instance.setLeaderToken(token);
        SessionStorage.instance.setActiveFlow('leader');
        SessionStorage.instance.setUrlHash('/leader');
        Navigator.of(context).pushReplacement(
          MaterialPageRoute(
            builder: (_) => LeaderScreen(
              client: widget.client,
              userProfile: me,
            ),
          ),
        );
      } else {
        SessionStorage.instance.setMemberToken(token);
        SessionStorage.instance.setActiveFlow('member');
        SessionStorage.instance.setUrlHash('/member');
        Navigator.of(context).pushReplacement(
          MaterialPageRoute(
            builder: (_) => MemberScreen(
              client: widget.client,
              userProfile: me,
            ),
          ),
        );
      }
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _errorMessage = e.toString();
        _isLoading = false;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('CrewLink Login'),
      ),
      body: Center(
        child: SingleChildScrollView(
          padding: const EdgeInsets.all(24.0),
          child: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 450),
            child: Form(
              key: _formKey,
              child: Column(
                mainAxisSize: MainAxisSize.min,
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  const Text(
                    'Sign in to CrewLink',
                    style: TextStyle(fontSize: 24, fontWeight: FontWeight.bold),
                    textAlign: TextAlign.center,
                  ),
                  const SizedBox(height: 24),
                  if (_errorMessage != null) ...[
                    Container(
                      padding: const EdgeInsets.all(12),
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
                    const SizedBox(height: 16),
                  ],
                  TextFormField(
                    key: const Key('server_url_field'),
                    controller: _serverUrlController,
                    decoration: const InputDecoration(
                      labelText: 'Server URL',
                      border: OutlineInputBorder(),
                      helperText: 'Default: http://localhost:8000',
                    ),
                    validator: (v) {
                      if (v == null || v.trim().isEmpty) {
                        return 'Server URL is required';
                      }
                      return null;
                    },
                  ),
                  const SizedBox(height: 16),
                  TextFormField(
                    key: const Key('username_field'),
                    controller: _usernameController,
                    decoration: const InputDecoration(
                      labelText: 'Username',
                      border: OutlineInputBorder(),
                    ),
                    validator: (v) {
                      if (v == null || v.trim().isEmpty) {
                        return 'Username is required';
                      }
                      return null;
                    },
                  ),
                  const SizedBox(height: 16),
                  TextFormField(
                    key: const Key('password_field'),
                    controller: _passwordController,
                    obscureText: true,
                    decoration: const InputDecoration(
                      labelText: 'Password',
                      border: OutlineInputBorder(),
                    ),
                    validator: (v) {
                      if (v == null || v.trim().isEmpty) {
                        return 'Password is required';
                      }
                      return null;
                    },
                  ),
                  const SizedBox(height: 24),
                  ElevatedButton(
                    key: const Key('login_button'),
                    onPressed: _isLoading ? null : _handleLogin,
                    style: ElevatedButton.styleFrom(
                      padding: const EdgeInsets.symmetric(vertical: 16),
                    ),
                    child: _isLoading
                        ? const SizedBox(
                            height: 20,
                            width: 20,
                            child: CircularProgressIndicator(strokeWidth: 2),
                          )
                        : const Text('Log In', style: TextStyle(fontSize: 16)),
                  ),
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }
}
