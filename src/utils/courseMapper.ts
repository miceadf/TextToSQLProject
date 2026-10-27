import type { CourseItemApi, WeekdayApi } from '../types/api'
import type { Course, Weekday } from '../types/course'
import { TIME_TBA_TEXT } from '../adaptive/chatQuery'
import {
  normalizeCourseDay,
  parseCourseClockHour,
  parseCourseSlotsFromText,
} from './courseSchedule'

const weekdayMap: Record<WeekdayApi, Weekday | null> = {
  MON: 'Mon',
  TUE: 'Tue',
  WED: 'Wed',
  THU: 'Thu',
  FRI: 'Fri',
  SAT: 'Sat',
  SUN: null,
}

function parseClockHour(value: string) {
  return parseCourseClockHour(value)
}

function apiStatus(course: CourseItemApi): Course['status'] {
  if (
    typeof course.capacity === 'number' &&
    typeof course.enrolled === 'number' &&
    course.capacity > 0 &&
    course.enrolled >= course.capacity
  ) {
    return 'Closed'
  }
  return 'Open'
}

export function apiCourseToCourse(course: CourseItemApi): Course {
  const slots = course.schedule
    .map((slot) => {
      const day = weekdayMap[slot.day] ?? normalizeCourseDay(String(slot.day))
      const startHour = parseClockHour(slot.start)
      const endHour = parseClockHour(slot.end)
      if (!day || startHour === null || endHour === null || endHour <= startHour) {
        return null
      }
      return { day, startHour, endHour }
    })
    .filter((slot): slot is Course['slots'][number] => slot !== null)

  const timeText =
    course.lectureTime ||
    course.schedule
      .map((slot) => `${slot.day} ${slot.start}-${slot.end}`)
      .join(', ') ||
    TIME_TBA_TEXT
  const parsedSlots = slots.length > 0 ? slots : parseCourseSlotsFromText(timeText)

  return {
    id: course.courseId,
    name: course.name,
    departmentName: course.departmentName,
    professor: course.professor,
    credits: course.credits,
    status: apiStatus(course),
    capacity: course.capacity,
    enrolled: course.enrolled,
    timeText,
    locationText: course.locationText,
    slots: parsedSlots,
    prerequisiteCourseCodes: course.prerequisiteCourseCodes,
    prerequisiteCourseNames: course.prerequisiteCourseNames,
    details: course.details,
  }
}
