import 'dart:async';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:flutter_member_callout/api/api_client.dart';
import 'package:flutter_member_callout/main.dart';
import 'package:flutter_member_callout/models/announcement.dart';
import 'package:flutter_member_callout/models/inbox_item.dart';
import 'package:flutter_member_callout/models/user_profile.dart';
import 'package:flutter_member_callout/screens/leader_screen.dart';
import 'package:flutter_member_callout/screens/login_screen.dart';
import 'package:flutter_member_callout/screens/member_screen.dart';
import 'package:flutter_member_callout/storage/session_storage.dart';

class FakeApiClient extends ApiClient {
  UserProfile? fakeMe;
  List<String> fakeClassifications = ['Foreman', 'Journeyman Wireman'];
  List<AnnouncementItem> fakeAnnouncements = [];
  AnnouncementStats? fakeStats;
  AIDraftResult? fakeAIDraftResult;
  bool shouldThrowAIUnavailable = false;
  PaginatedInbox? fakeInbox;

  final List<String> calls = [];
  final List<String> receiptsDeliveredSent = [];
  final List<String> receiptsReadSent = [];
  final List<String> acknowledgedIds = [];
  final List<Map<String, String>> responsesRecorded = [];
  int statsFetchCount = 0;
  Completer<void>? pendingStatsCompleter;

  FakeApiClient() : super(baseUrl: 'http://localhost:8000');

  @override
  Future<UserProfile> getMe() async {
    calls.add('getMe');
    return fakeMe ??
        UserProfile(
          id: 'user-1',
          fullName: 'Test Leader',
          email: 'leader@example.com',
          classification: 'Foreman',
          localId: 'local-27',
          localName: 'Local 27',
          leadershipRoles: [
            LeadershipRole(
              id: 1,
              localId: 'local-27',
              localName: 'Local 27',
              role: 'leader',
            ),
          ],
        );
  }

  @override
  Future<List<String>> getClassifications(String localId) async {
    calls.add('getClassifications');
    return fakeClassifications;
  }

  @override
  Future<List<AnnouncementItem>> getAnnouncements() async {
    calls.add('getAnnouncements');
    return fakeAnnouncements;
  }

  @override
  Future<AnnouncementItem> getAnnouncement(String id) async {
    calls.add('getAnnouncement:$id');
    return fakeAnnouncements.firstWhere((a) => a.id == id);
  }

  @override
  Future<AnnouncementStats> getStats(String id) async {
    calls.add('getStats:$id');
    statsFetchCount++;
    if (pendingStatsCompleter != null) {
      await pendingStatsCompleter!.future;
    }
    return fakeStats ??
        AnnouncementStats(
          targeted: 1584,
          sent: 1584,
          pushFailed: 55,
          delivered: 1424,
          read: 1024,
          acknowledged: 719,
          coming: 341,
          cantMakeIt: 159,
          noResponse: 1084,
          status: 'sent',
          asOf: '2026-10-26T12:00:00Z',
        );
  }

  @override
  Future<AIDraftResult> draftWithAI(
    String localId,
    String note, {
    String? idempotencyKey,
  }) async {
    calls.add('draftWithAI');
    if (shouldThrowAIUnavailable) {
      throw ApiException(
        'AI service is currently unavailable. Please draft manually.',
        statusCode: 503,
        errorCode: 'ai_unavailable',
      );
    }
    return fakeAIDraftResult ??
        AIDraftResult(
          title: 'AI Draft Title',
          body: 'AI Draft Body',
          pushPreview: 'AI Push Preview',
          warnings: ['Fact-check warning 1'],
        );
  }

  @override
  Future<AnnouncementItem> saveDraft({
    String? id,
    required String title,
    required String body,
    String? pushPreview,
    String? source,
    String? aiNote,
    String? aiModel,
    List<String>? audienceClassifications,
    String? idempotencyKey,
  }) async {
    calls.add('saveDraft');
    return AnnouncementItem(
      id: id ?? 'draft-123',
      localId: 'local-27',
      title: title,
      body: body,
      needsAck: false,
      status: 'draft',
      source: source ?? 'manual',
      audienceClassifications: audienceClassifications ?? [],
      pushPreview: pushPreview ?? '',
      createdAt: DateTime.now().toIso8601String(),
    );
  }

  @override
  Future<AnnouncementItem> approve(String id) async {
    calls.add('approve:$id');
    return AnnouncementItem(
      id: id,
      localId: 'local-27',
      title: 'AI Draft Title',
      body: 'AI Draft Body',
      needsAck: false,
      status: 'draft',
      source: 'ai',
      audienceClassifications: [],
      pushPreview: 'AI Push Preview',
      approvedAt: DateTime.now().toIso8601String(),
      createdAt: DateTime.now().toIso8601String(),
    );
  }

  @override
  Future<Map<String, dynamic>> send(String id, {String? idempotencyKey}) async {
    calls.add('send:$id');
    return {'status': 'sending'};
  }

  @override
  Future<PaginatedInbox> getInbox({String? cursorUrl}) async {
    calls.add('getInbox');
    return fakeInbox ?? PaginatedInbox(items: []);
  }

  @override
  Future<int> sendReceipts(
    List<String> announcementIds, {
    String kind = 'delivered',
  }) async {
    calls.add('sendReceipts:$kind');
    if (kind == 'delivered') {
      receiptsDeliveredSent.addAll(announcementIds);
    } else if (kind == 'read') {
      receiptsReadSent.addAll(announcementIds);
    }
    return announcementIds.length;
  }

  @override
  Future<void> acknowledge(String announcementId) async {
    calls.add('acknowledge:$announcementId');
    acknowledgedIds.add(announcementId);
  }

  @override
  Future<void> respond(String announcementId, String responseValue) async {
    calls.add('respond:$announcementId:$responseValue');
    responsesRecorded.add({'id': announcementId, 'response': responseValue});
  }
}

UserProfile _makeLeaderProfile() {
  return UserProfile(
    id: 'leader-1',
    fullName: 'Leader Name',
    email: 'leader@example.com',
    classification: 'Foreman',
    localId: 'local-27',
    localName: 'Local 27',
    leadershipRoles: [
      LeadershipRole(
        id: 1,
        localId: 'local-27',
        localName: 'Local 27',
        role: 'leader',
      ),
    ],
  );
}

UserProfile _makeMemberProfile() {
  return UserProfile(
    id: 'member-1',
    fullName: 'Member Name',
    email: 'member@example.com',
    classification: 'Journeyman Wireman',
    localId: 'local-27',
    localName: 'Local 27',
    leadershipRoles: [],
  );
}

void main() {
  void setDesktopSize(WidgetTester tester) {
    tester.view.physicalSize = const Size(1200, 1600);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(() {
      tester.view.resetPhysicalSize();
      tester.view.resetDevicePixelRatio();
    });
  }

  // ── Test 1: Send is disabled for an unapproved AI draft and enabled after approve
  testWidgets(
    'Send is disabled for unapproved AI draft and enabled after approve',
    (tester) async {
      setDesktopSize(tester);
      final client = FakeApiClient();
      final profile = _makeLeaderProfile();

      await tester.pumpWidget(
        MaterialApp(
          home: LeaderScreen(client: client, userProfile: profile),
        ),
      );
      await tester.pumpAndSettle();

      // Enter note and tap Draft with AI
      await tester.enterText(
        find.byKey(const Key('ai_note_field')),
        'Emergency meeting tonight at 6pm',
      );
      await tester.ensureVisible(find.byKey(const Key('draft_with_ai_button')));
      await tester.tap(find.byKey(const Key('draft_with_ai_button')));
      await tester.pumpAndSettle();

      // Verify AI draft is loaded and marked "Not approved"
      expect(find.text('Not approved'), findsOneWidget);

      // Save the draft first so it exists
      await tester.ensureVisible(find.byKey(const Key('save_draft_button')));
      await tester.tap(find.byKey(const Key('save_draft_button')));
      await tester.pumpAndSettle();

      // Verify Send button is disabled
      final sendButtonBefore = tester.widget<ElevatedButton>(
        find.byKey(const Key('send_button')),
      );
      expect(sendButtonBefore.onPressed, isNull);

      // Tap Approve
      await tester.ensureVisible(find.byKey(const Key('approve_button')));
      await tester.tap(find.byKey(const Key('approve_button')));
      await tester.pumpAndSettle();

      // Confirmation dialog appears
      expect(find.text('Confirm AI Draft Approval'), findsOneWidget);
      await tester.tap(find.byKey(const Key('confirm_approve_button')));
      await tester.pumpAndSettle();

      // Status chip should now be Approved
      expect(find.text('Approved'), findsOneWidget);

      // Send button should now be enabled
      final sendButtonAfter = tester.widget<ElevatedButton>(
        find.byKey(const Key('send_button')),
      );
      expect(sendButtonAfter.onPressed, isNotNull);
    },
  );

  // ── Test 2: Editing an approved AI draft disables Send again
  testWidgets('Editing an approved AI draft disables Send again', (
    tester,
  ) async {
    setDesktopSize(tester);
    final client = FakeApiClient();
    final profile = _makeLeaderProfile();

    await tester.pumpWidget(
      MaterialApp(
        home: LeaderScreen(client: client, userProfile: profile),
      ),
    );
    await tester.pumpAndSettle();

    // Draft with AI
    await tester.enterText(
      find.byKey(const Key('ai_note_field')),
      'Draft text',
    );
    await tester.ensureVisible(find.byKey(const Key('draft_with_ai_button')));
    await tester.tap(find.byKey(const Key('draft_with_ai_button')));
    await tester.pumpAndSettle();

    // Save and Approve
    await tester.ensureVisible(find.byKey(const Key('save_draft_button')));
    await tester.tap(find.byKey(const Key('save_draft_button')));
    await tester.pumpAndSettle();
    await tester.ensureVisible(find.byKey(const Key('approve_button')));
    await tester.tap(find.byKey(const Key('approve_button')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('confirm_approve_button')));
    await tester.pumpAndSettle();

    expect(find.text('Approved'), findsOneWidget);
    var sendButton = tester.widget<ElevatedButton>(
      find.byKey(const Key('send_button')),
    );
    expect(sendButton.onPressed, isNotNull);

    // Edit title field
    await tester.ensureVisible(find.byKey(const Key('title_field')));
    await tester.enterText(
      find.byKey(const Key('title_field')),
      'Edited Title After Approval',
    );
    await tester.pumpAndSettle();

    // Should be Not approved again and Send button disabled
    expect(find.text('Not approved'), findsOneWidget);
    sendButton = tester.widget<ElevatedButton>(
      find.byKey(const Key('send_button')),
    );
    expect(sendButton.onPressed, isNull);
  });

  // ── Test 3: An ai_unavailable error shows the message and manual form still saves and sends
  testWidgets(
    'ai_unavailable error shows message and manual form still saves and sends',
    (tester) async {
      setDesktopSize(tester);
      final client = FakeApiClient();
      client.shouldThrowAIUnavailable = true;
      final profile = _makeLeaderProfile();

      await tester.pumpWidget(
        MaterialApp(
          home: LeaderScreen(client: client, userProfile: profile),
        ),
      );
      await tester.pumpAndSettle();

      // Try AI drafting
      await tester.enterText(
        find.byKey(const Key('ai_note_field')),
        'Testing error path',
      );
      await tester.ensureVisible(find.byKey(const Key('draft_with_ai_button')));
      await tester.tap(find.byKey(const Key('draft_with_ai_button')));
      await tester.pumpAndSettle();

      // Verify error message is displayed
      expect(
        find.text(
          'AI service is currently unavailable. Please draft manually.',
        ),
        findsOneWidget,
      );

      // Enter manual form fields
      await tester.ensureVisible(find.byKey(const Key('title_field')));
      await tester.enterText(
        find.byKey(const Key('title_field')),
        'Manual Callout Title',
      );
      await tester.enterText(
        find.byKey(const Key('body_field')),
        'Manual Callout Body content',
      );
      await tester.pumpAndSettle();

      // Tap Save Draft
      await tester.ensureVisible(find.byKey(const Key('save_draft_button')));
      await tester.tap(find.byKey(const Key('save_draft_button')));
      await tester.pumpAndSettle();

      // Manual draft should be enabled for Send
      final sendButton = tester.widget<ElevatedButton>(
        find.byKey(const Key('send_button')),
      );
      expect(sendButton.onPressed, isNotNull);

      // Tap Send
      await tester.ensureVisible(find.byKey(const Key('send_button')));
      await tester.tap(find.byKey(const Key('send_button')));
      await tester.pumpAndSettle();

      // Confirmation dialog appears
      expect(find.text('Confirm Send Callout'), findsOneWidget);
      await tester.tap(find.byKey(const Key('confirm_send_button')));
      await tester.pumpAndSettle();

      expect(client.calls.contains('send:draft-123'), isTrue);
    },
  );

  // ── Test 4: The counts table renders values from stats
  testWidgets('Counts table renders values from stats', (tester) async {
    setDesktopSize(tester);
    final client = FakeApiClient();
    final profile = _makeLeaderProfile();
    client.fakeStats = AnnouncementStats(
      targeted: 1584,
      sent: 1584,
      pushFailed: 55,
      delivered: 1424,
      read: 1024,
      acknowledged: 719,
      coming: 341,
      cantMakeIt: 159,
      noResponse: 1084,
      status: 'sent',
      asOf: '2026-10-26T12:00:00Z',
    );

    final item = AnnouncementItem(
      id: 'sent-ann-1',
      localId: 'local-27',
      title: 'Sent Announcement',
      body: 'Body',
      needsAck: false,
      status: 'sent',
      source: 'manual',
      audienceClassifications: [],
      pushPreview: '',
      createdAt: '2026-10-26T10:00:00Z',
    );
    client.fakeAnnouncements = [item];

    await tester.pumpWidget(
      MaterialApp(
        home: LeaderScreen(client: client, userProfile: profile),
      ),
    );
    await tester.pumpAndSettle();

    // Select the existing sent announcement from dropdown
    final state = tester.state<LeaderScreenState>(find.byType(LeaderScreen));
    state.selectAnnouncement(item);
    await state.fetchStats();
    await tester.pumpAndSettle();

    // Verify all stats values are rendered
    expect(find.byKey(const Key('stats_card')), findsOneWidget);
    expect(find.text('1584'), findsNWidgets(2)); // targeted and sent
    expect(find.text('55'), findsOneWidget); // pushFailed
    expect(find.text('1424'), findsOneWidget); // delivered
    expect(find.text('1024'), findsOneWidget); // read
    expect(find.text('719'), findsOneWidget); // acknowledged
    expect(find.text('341'), findsOneWidget); // coming
    expect(find.text('159'), findsOneWidget); // cantMakeIt
    expect(find.text('1084'), findsOneWidget); // noResponse
  });

  // ── Test 5: Member screen sends delivered receipt on load, read on open, and acknowledge on Got it
  testWidgets(
    'Member screen sends delivered receipt, read receipt on open, and calls acknowledge',
    (tester) async {
      setDesktopSize(tester);
      final client = FakeApiClient();
      final profile = _makeMemberProfile();

      final item = InboxItem(
        id: 1,
        announcementId: 'ann-item-99',
        announcementTitle: 'Important Callout',
        announcementBody: 'Job dispatch details at location.',
        announcementStartsAt: '2026-10-27 08:00:00',
        announcementNeedsAck: true,
        deliveredAt: null, // Not yet delivered
        readAt: null,
        acknowledgedAt: null,
      );
      client.fakeInbox = PaginatedInbox(items: [item]);

      await tester.pumpWidget(
        MaterialApp(
          home: MemberScreen(client: client, userProfile: profile),
        ),
      );
      await tester.pumpAndSettle();

      // 1. Loading the inbox sent ONE batch receipt of kind "delivered"
      expect(client.receiptsDeliveredSent, contains('ann-item-99'));

      // 2. Open the item
      await tester.tap(find.byKey(const Key('inbox_item_ann-item-99')));
      await tester.pumpAndSettle();

      // Opening it sends a receipt of kind "read"
      expect(client.receiptsReadSent, contains('ann-item-99'));

      // 3. Tap "Got it" button
      expect(find.byKey(const Key('got_it_button')), findsOneWidget);
      await tester.tap(find.byKey(const Key('got_it_button')));
      await tester.pumpAndSettle();

      // Acknowledge was called
      expect(client.acknowledgedIds, contains('ann-item-99'));

      // 4. Test "Coming" button for meeting announcement
      expect(find.byKey(const Key('coming_button')), findsOneWidget);
      await tester.tap(find.byKey(const Key('coming_button')));
      await tester.pumpAndSettle();

      expect(
        client.responsesRecorded.any(
          (r) => r['id'] == 'ann-item-99' && r['response'] == 'coming',
        ),
        isTrue,
      );
    },
  );

  // ── Test 6: Polling timer does not overlap requests and is cancelled on dispose
  testWidgets(
    'Polling timer does not overlap requests and is cancelled on dispose',
    (tester) async {
      setDesktopSize(tester);
      final client = FakeApiClient();
      final profile = _makeLeaderProfile();
      client.pendingStatsCompleter = Completer<void>();

      await tester.pumpWidget(
        MaterialApp(
          home: LeaderScreen(client: client, userProfile: profile),
        ),
      );
      await tester.pumpAndSettle();

      final state = tester.state<LeaderScreenState>(find.byType(LeaderScreen));

      // Select an announcement to poll
      final item = AnnouncementItem(
        id: 'ann-poll-1',
        localId: 'local-27',
        title: 'Poll Test',
        body: 'Body',
        needsAck: false,
        status: 'sending',
        source: 'manual',
        audienceClassifications: [],
        pushPreview: '',
        createdAt: '2026-10-26T10:00:00Z',
      );
      state.selectAnnouncement(item);

      // Call fetchStats twice concurrently
      final future1 = state.fetchStats();
      final future2 = state.fetchStats();

      // Only one should have initiated because _isPollingStats guards against overlap
      expect(client.statsFetchCount, equals(1));

      // Release the completer
      client.pendingStatsCompleter!.complete();
      await future1;
      await future2;

      // Disposing screen cancels the polling timer
      await tester.pumpWidget(
        const MaterialApp(home: Scaffold(body: Text('Different Screen'))),
      );
      await tester.pumpAndSettle();

      // After dispose, no further polling calls happen
      final countAfterDispose = client.statsFetchCount;
      await tester.pump(const Duration(seconds: 30));
      expect(client.statsFetchCount, equals(countAfterDispose));
    },
  );

  // ── Test 7: Dropdown status updates to sent immediately after send and approval status persists
  testWidgets(
    'Dropdown status updates immediately on send and approval status persists',
    (tester) async {
      setDesktopSize(tester);
      final client = FakeApiClient();
      final profile = _makeLeaderProfile();

      await tester.pumpWidget(
        MaterialApp(
          home: LeaderScreen(client: client, userProfile: profile),
        ),
      );
      await tester.pumpAndSettle();

      // 1. Draft with AI
      await tester.enterText(
        find.byKey(const Key('ai_note_field')),
        'Drafting safety notification',
      );
      await tester.ensureVisible(find.byKey(const Key('draft_with_ai_button')));
      await tester.tap(find.byKey(const Key('draft_with_ai_button')));
      await tester.pumpAndSettle();

      // 2. Save & Approve
      await tester.ensureVisible(find.byKey(const Key('save_draft_button')));
      await tester.tap(find.byKey(const Key('save_draft_button')));
      await tester.pumpAndSettle();

      await tester.ensureVisible(find.byKey(const Key('approve_button')));
      await tester.tap(find.byKey(const Key('approve_button')));
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const Key('confirm_approve_button')));
      await tester.pumpAndSettle();

      expect(find.text('Approved'), findsOneWidget);

      // 3. Send
      await tester.ensureVisible(find.byKey(const Key('send_button')));
      await tester.tap(find.byKey(const Key('send_button')));
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const Key('confirm_send_button')));
      await tester.pumpAndSettle();

      // Status chip must still be Approved
      expect(find.text('Approved'), findsOneWidget);

      // Dropdown should immediately display (sent)
      expect(find.textContaining('(sent)'), findsWidgets);
    },
  );

  // ── Test 8: Member inbox pull-to-refresh and refresh button reload items
  testWidgets('Member inbox pull-to-refresh reloads inbox items', (tester) async {
    setDesktopSize(tester);
    final client = FakeApiClient();
    final profile = _makeMemberProfile();

    client.fakeInbox = PaginatedInbox(items: []);

    await tester.pumpWidget(
      MaterialApp(
        home: MemberScreen(client: client, userProfile: profile),
      ),
    );
    await tester.pumpAndSettle();

    expect(find.text('No announcements in your inbox.'), findsOneWidget);

    // Now a new announcement arrives
    client.fakeInbox = PaginatedInbox(items: [
      InboxItem(
        id: 2,
        announcementId: 'ann-new-1',
        announcementTitle: 'Newly Published Callout',
        announcementBody: 'Important details.',
        announcementStartsAt: null,
        announcementNeedsAck: true,
        deliveredAt: null,
        readAt: null,
        acknowledgedAt: null,
      ),
    ]);

    // Tap refresh button
    expect(find.byKey(const Key('refresh_inbox_button')), findsOneWidget);
    await tester.tap(find.byKey(const Key('refresh_inbox_button')));
    await tester.pumpAndSettle();

    expect(find.text('Newly Published Callout'), findsOneWidget);
  });

  // ── Test 9: Session restoration restores leader and member sessions separately
  testWidgets('Session restoration restores leader and member sessions separately', (tester) async {
    setDesktopSize(tester);
    final client = FakeApiClient();

    // 1. Set leader session
    SessionStorage.instance.setLeaderToken('leader-token-123');
    SessionStorage.instance.setActiveFlow('leader');
    SessionStorage.instance.setUrlHash('/leader');
    client.fakeMe = _makeLeaderProfile();

    await tester.pumpWidget(CrewLinkApp(key: UniqueKey(), client: client));
    await tester.pumpAndSettle();

    // Verifies LeaderScreen is restored without showing LoginScreen
    expect(find.byType(LeaderScreen), findsOneWidget);
    expect(find.byType(LoginScreen), findsNothing);

    // 2. Set member session
    SessionStorage.instance.setMemberToken('member-token-456');
    SessionStorage.instance.setActiveFlow('member');
    SessionStorage.instance.setUrlHash('/member');
    client.fakeMe = _makeMemberProfile();

    await tester.pumpWidget(CrewLinkApp(key: UniqueKey(), client: client));
    await tester.pumpAndSettle();

    // Verifies MemberScreen is restored without showing LoginScreen
    expect(find.byType(MemberScreen), findsOneWidget);
    expect(find.byType(LoginScreen), findsNothing);
  });

  // ── Test 10: Local dropdown displays logged-in local name and is not empty
  testWidgets('Leader screen local dropdown displays logged-in local name', (tester) async {
    setDesktopSize(tester);
    final client = FakeApiClient();
    final profile = _makeLeaderProfile();

    await tester.pumpWidget(
      MaterialApp(
        home: LeaderScreen(client: client, userProfile: profile),
      ),
    );
    await tester.pumpAndSettle();

    // Verify local picker is present and displays 'Local 27'
    expect(find.byKey(const Key('local_picker')), findsOneWidget);
    expect(find.text('Local 27'), findsWidgets);
  });

  // ── Test 11: Member screen displays Got it, Coming, and Can't make it for every announcement
  testWidgets(
    'Member screen displays Got it, Coming, and Cant make it buttons even without meeting time',
    (tester) async {
      setDesktopSize(tester);
      final client = FakeApiClient();
      final profile = _makeMemberProfile();

      final item = InboxItem(
        id: 2,
        announcementId: 'ann-plain-101',
        announcementTitle: 'General Announcement Without Meeting',
        announcementBody: 'Regular update for all members.',
        announcementStartsAt: null, // NO meeting time
        announcementNeedsAck: true,
        deliveredAt: '2026-10-26 10:00:00',
        readAt: null,
        acknowledgedAt: null,
      );
      client.fakeInbox = PaginatedInbox(items: [item]);

      await tester.pumpWidget(
        MaterialApp(
          home: MemberScreen(client: client, userProfile: profile),
        ),
      );
      await tester.pumpAndSettle();

      // Open item detail
      await tester.tap(find.byKey(const Key('inbox_item_ann-plain-101')));
      await tester.pumpAndSettle();

      // All three buttons MUST be displayed
      expect(find.byKey(const Key('got_it_button')), findsOneWidget);
      expect(find.byKey(const Key('coming_button')), findsOneWidget);
      expect(find.byKey(const Key('cant_make_it_button')), findsOneWidget);

      // Tap Coming
      await tester.tap(find.byKey(const Key('coming_button')));
      await tester.pumpAndSettle();

      expect(
        client.responsesRecorded.any(
          (r) => r['id'] == 'ann-plain-101' && r['response'] == 'coming',
        ),
        isTrue,
      );
    },
  );

  // ── Test 12: Sent announcement displays pinned announcement header
  testWidgets('Sent announcement displays pinned announcement header', (tester) async {
    setDesktopSize(tester);
    final client = FakeApiClient();
    final profile = _makeLeaderProfile();
    client.fakeStats = AnnouncementStats(
      targeted: 1584,
      sent: 1584,
      pushFailed: 0,
      delivered: 1500,
      read: 1200,
      acknowledged: 900,
      coming: 500,
      cantMakeIt: 200,
      noResponse: 884,
      status: 'sent',
      asOf: '2026-10-26T12:00:00Z',
    );

    final item = AnnouncementItem(
      id: 'sent-ann-persistent-1',
      localId: 'local-27',
      title: 'Persistent Header Test Callout',
      body: 'This message must remain visible while scrolling stats.',
      needsAck: true,
      status: 'sent',
      source: 'manual',
      audienceClassifications: [],
      pushPreview: 'Test Preview',
      createdAt: '2026-10-26T10:00:00Z',
    );
    client.fakeAnnouncements = [item];

    await tester.pumpWidget(
      MaterialApp(
        home: LeaderScreen(client: client, userProfile: profile),
      ),
    );
    await tester.pumpAndSettle();

    final state = tester.state<LeaderScreenState>(find.byType(LeaderScreen));
    state.selectAnnouncement(item);
    await state.fetchStats();
    await tester.pumpAndSettle();

    // Verify pinned announcement header is visible
    expect(find.byKey(const Key('pinned_announcement_header')), findsOneWidget);
    expect(find.text('Persistent Header Test Callout'), findsWidgets);
    expect(find.text('This message must remain visible while scrolling stats.'), findsWidgets);

    // Scroll to bottom
    final scrollFinder = find.byKey(const Key('leader_screen_scroll_view'));
    await tester.drag(scrollFinder, const Offset(0, -500));
    await tester.pumpAndSettle();

    // Pinned header is still visible after scrolling
    expect(find.byKey(const Key('pinned_announcement_header')), findsOneWidget);
    expect(find.byKey(const Key('stats_card')), findsOneWidget);
  });
}
