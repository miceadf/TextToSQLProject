const LABELS: Record<string, string> = {
  'Course.code': '과목 코드', 'Course.name': '과목명', 'Course.credits': '학점', 'Course.category': '이수 구분',
  'Department.code': '학과 코드', 'Department.name': '학과명', 'Instructor.name': '교수명',
  'CourseOffering.semester': '학기', 'CourseOffering.section': '분반',
  'CourseOffering.capacity': '정원', 'CourseOffering.enrolled': '수강 인원', 'Course.target_grade': '대상 학년',
  'Schedule.day': '요일', 'Schedule.start_time': '시작 시간', 'Schedule.end_time': '종료 시간',
};

export function reviewFieldName(field: string): string {
  return LABELS[field] ?? field;
}
