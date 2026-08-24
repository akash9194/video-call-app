import React from 'react';
import { createNavigationContainerRef } from '@react-navigation/native';
import { NativeStackNavigationProp } from '@react-navigation/native-stack';
import { CallMedia } from '../types';

export type RootStackParamList = {
  Login: undefined;
  Signup: undefined;
  Home: undefined;
  Schedule: { patientId: string; patientName: string };
  Call: { callId: string; remoteUserId: string; remoteUserName: string; isCaller: boolean };
  IncomingCall: { callId: string; fromUserId: string; fromUserName: string; media: CallMedia };
  // Epic §30 -- shown after a call that actually connected ends, doctor
  // side only (see CallContext's finishCall). callId is the now-ended
  // call whose /notes endpoint this screen PATCHes.
  PostCallNotes: { callId: string; remoteUserName: string };
};

export type NavProp = NativeStackNavigationProp<RootStackParamList>;

// Lets code outside of React components (e.g. CallContext reacting to a
// WebSocket event) trigger navigation — e.g. jump to the IncomingCall
// screen the instant a call comes in, no matter what screen is on top.
export const navigationRef = createNavigationContainerRef<RootStackParamList>();

export function navigate(name: keyof RootStackParamList, params?: any) {
  if (navigationRef.isReady()) {
    navigationRef.navigate(name as any, params);
  }
}
