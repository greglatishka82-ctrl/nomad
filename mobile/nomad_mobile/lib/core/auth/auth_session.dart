import 'package:flutter_riverpod/flutter_riverpod.dart';

/// Increments whenever an API request proves that the saved mobile session is
/// no longer valid. An integer is used instead of a boolean so every later
/// expiration is delivered to listeners as a new event.
final authSessionInvalidationProvider = StateProvider<int>((ref) => 0);
